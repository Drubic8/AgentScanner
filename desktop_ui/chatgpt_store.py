"""Account-separated ChatGPT sessions protected for the current Windows user."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
from threading import RLock, local
import time
from uuid import UUID, uuid4

from .access_store import protect
from .storage import atomic_write
from miner_scanner.ai.contracts import AnalysisError

MAGIC = b'ASIC-CHATGPT-1\n'
MAX_BYTES = 512 * 1024
_LOCKS = {}
_DEPTHS = {}
_LOCKS_GUARD = RLock()
_TOKEN_FIELDS = ('access_token', 'refresh_token', 'id_token')
_FIELDS = {'issuer', 'subject', 'email', 'client_id', 'ext_agent_host_id', 'id_token',
           'access_token', 'refresh_token', 'token_type', 'scopes', 'expires_at', 'saved_at', 'welcome_seen'}


def _validate(record):
    if not isinstance(record, dict) or set(record) - _FIELDS:
        raise ValueError('Invalid protected ChatGPT account')
    value = deepcopy(record)
    if value.get('issuer') != 'https://auth.openai.com':
        raise ValueError('Invalid protected ChatGPT account')
    for key in ('subject', 'client_id', 'ext_agent_host_id'):
        if not isinstance(value.get(key), str) or not value[key] or len(value[key]) > 256:
            raise ValueError('Invalid protected ChatGPT account')
    if value['client_id'] == 'dynamic_agent_client':
        raise ValueError('Invalid protected ChatGPT account')
    for key in ('email', 'token_type', 'saved_at', *_TOKEN_FIELDS):
        text = value.get(key, '')
        if not isinstance(text, str) or len(text) > (32768 if key in _TOKEN_FIELDS else 512):
            raise ValueError('Invalid protected ChatGPT account')
        if any(ord(char) < 32 for char in text):
            raise ValueError('Invalid protected ChatGPT account')
        value[key] = text
    expiry = value.get('expires_at', 0)
    if isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not math.isfinite(expiry) or expiry < 0:
        raise ValueError('Invalid protected ChatGPT account')
    value['expires_at'] = expiry
    scopes = value.get('scopes', [])
    if not isinstance(scopes, list) or len(scopes) > 32 or any(not isinstance(s, str) or not s or len(s) > 128 for s in scopes):
        raise ValueError('Invalid protected ChatGPT account')
    value['scopes'] = list(dict.fromkeys(scopes))
    value['welcome_seen'] = value.get('welcome_seen') is True
    return value


class ChatGPTStore:
    def __init__(self, path, crypt=protect):
        self.path = Path(path)
        self.crypt = crypt
        with _LOCKS_GUARD:
            key = str(self.path.resolve()).casefold()
            self._lock = _LOCKS.setdefault(key, RLock())
            self._depth = _DEPTHS.setdefault(key, local())

    def __repr__(self):
        return 'ChatGPTStore(<protected sessions>)'

    @contextmanager
    def _thread_locked(self, cancel):
        until = time.monotonic() + 15
        while not self._lock.acquire(timeout=0.05):
            if cancel is not None and cancel.is_set():
                raise AnalysisError('cancelled')
            if time.monotonic() >= until:
                raise AnalysisError('auth_busy')
        try:
            if cancel is not None and cancel.is_set():
                raise AnalysisError('cancelled')
            yield
        finally:
            self._lock.release()

    @contextmanager
    def locked(self, cancel=None):
        # RLock protects threads, OS locks serialize refresh-token rotation
        # across separate running instances. The OS releases locks on exit.
        with self._thread_locked(cancel):
            if getattr(self._depth, 'value', 0):
                self._depth.value += 1
                try:
                    yield
                finally:
                    self._depth.value -= 1
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.with_suffix(self.path.suffix + '.lock').open('a+b') as stream:
                if stream.tell() == 0:
                    stream.write(b'0')
                    stream.flush()
                until = time.monotonic() + 15
                while True:
                    if cancel is not None and cancel.is_set():
                        raise AnalysisError('cancelled')
                    try:
                        stream.seek(0)
                        if os.name == 'nt':
                            import msvcrt
                            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError:
                        if time.monotonic() >= until:
                            raise AnalysisError('auth_busy') from None
                        time.sleep(0.05)
                try:
                    self._depth.value = 1
                    yield
                finally:
                    self._depth.value = 0
                    stream.seek(0)
                    if os.name == 'nt':
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _read(self):
        try:
            with self.path.open('rb') as stream:
                raw = stream.read(MAX_BYTES + 1)
        except FileNotFoundError:
            return dict(version=1, active_id=None, accounts=[])
        except OSError:
            raise OSError('Cannot read protected ChatGPT sessions') from None
        try:
            if not raw.startswith(MAGIC) or len(raw) > MAX_BYTES:
                raise ValueError()
            clear = self.crypt(raw[len(MAGIC):], decrypt=True)
            if not isinstance(clear, bytes) or len(clear) > MAX_BYTES:
                raise ValueError()
            value = json.loads(clear)
            if not isinstance(value, dict) or set(value) != {'version', 'active_id', 'accounts'} or type(value['version']) is not int or value['version'] != 1:
                raise ValueError()
            if not isinstance(value['accounts'], list) or len(value['accounts']) > 20:
                raise ValueError()
            value['accounts'] = [_validate(record) for record in value['accounts']]
            ids = [record['client_id'] for record in value['accounts']]
            if len(ids) != len(set(ids)) or value['active_id'] is not None and value['active_id'] not in ids:
                raise ValueError()
            return value
        except Exception:
            raise ValueError('Cannot decrypt ChatGPT sessions for this Windows account') from None

    def _write(self, value):
        try:
            clear = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
            if len(clear) > MAX_BYTES:
                raise ValueError()
            encrypted = self.crypt(clear)
            if not isinstance(encrypted, bytes) or not encrypted or len(encrypted) + len(MAGIC) > MAX_BYTES:
                raise ValueError()
            atomic_write(self.path, MAGIC + encrypted)
            os.chmod(self.path, 0o600)
        except Exception:
            raise OSError('Cannot protect ChatGPT sessions with Windows DPAPI') from None

    def load(self):
        value = self._read()
        return next((deepcopy(item) for item in value['accounts'] if item['client_id'] == value['active_id']), None)

    def get(self, client_id):
        return next((deepcopy(item) for item in self._read()['accounts'] if item['client_id'] == client_id), None)

    def accounts(self):
        return [dict(client_id=item['client_id'], email=item['email'], subject=item['subject'],
                     enabled=bool(item['access_token'] and 'chatgpt.tokens.use.direct' in item['scopes']))
                for item in self._read()['accounts']]

    def save(self, record):
        updated = _validate(record)
        with self.locked():
            value = self._read()
            previous = next((item for item in value['accounts'] if item['client_id'] == updated['client_id']), None)
            if previous:
                if (previous['issuer'], previous['subject']) != (updated['issuer'], updated['subject']):
                    raise ValueError('ChatGPT account identity does not match its registration')
                updated['welcome_seen'] = previous['welcome_seen'] or updated['welcome_seen']
                value['accounts'].remove(previous)
            elif len(value['accounts']) >= 20:
                raise ValueError('Too many protected ChatGPT accounts')
            value['accounts'].append(updated)
            value['active_id'] = updated['client_id']
            self._write(value)

    def select(self, client_id):
        with self.locked():
            value = self._read()
            if client_id not in {item['client_id'] for item in value['accounts']}:
                raise ValueError('Unknown protected ChatGPT account')
            value['active_id'] = client_id
            self._write(value)

    def clear_tokens(self, client_id=None):
        with self.locked():
            value = self._read()
            target = client_id or value['active_id']
            for item in value['accounts']:
                if item['client_id'] == target:
                    for name in _TOKEN_FIELDS:
                        item[name] = ''
                    item.update(scopes=[], expires_at=0)
            self._write(value)

    def host_id(self):
        with self.locked():
            path = self.path.with_name('.chatgpt-host.json')
            try:
                with path.open('rb') as stream:
                    raw = stream.read(1025)
                if len(raw) > 1024:
                    raise ValueError()
                value = json.loads(raw)['host_id']
                if not isinstance(value, str) or not value.startswith('urn:uuid:') or str(UUID(value[9:])) != value[9:]:
                    raise ValueError()
                return value
            except FileNotFoundError:
                value = 'urn:uuid:' + str(uuid4())
                atomic_write(path, json.dumps(dict(host_id=value)).encode('ascii'))
                os.chmod(path, 0o600)
                return value
            except (ValueError, KeyError, TypeError, OSError):
                raise ValueError('Cannot read ChatGPT host identifier') from None

    def has_welcome(self, client_id):
        value = self.get(client_id)
        return bool(value and value['welcome_seen'])

    def mark_welcome(self, client_id):
        with self.locked():
            value = self._read()
            for item in value['accounts']:
                if item['client_id'] == client_id:
                    item['welcome_seen'] = True
            self._write(value)
