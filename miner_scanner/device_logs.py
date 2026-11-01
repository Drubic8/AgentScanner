"""Bounded, on-demand device log reads. No mining/control endpoints are used.

Adapters deliberately return plain text, never execute or render device HTML.
Their availability is provisional until the selected device answers the read.
See docs/development/device_logs.md for endpoint evidence and limitations.
"""
from dataclasses import dataclass, field
from ipaddress import ip_address
import json
from pathlib import Path
import re
import struct
from threading import Event

import requests

from .access import AccessProfiles, standard_profiles
from .models import utc_now
from .runtime import (AuthenticationError, Cancelled, DeadlineExceeded,
                      Operation, ProtocolError, ScanOptions)
from .transports import Transport
from .whatsminer_log_archive import (MAX_ARCHIVE_BYTES, LogArchiveLimitError,
    MissingLogError, extract_whatsminer_log)


MAX_LOG_BYTES = 2 * 1024 * 1024
LOG_TIMEOUT_SECONDS = 20.0
WHATSMINER_LOG_TIMEOUT_SECONDS = 60.0
_MISSING = {404, 405, 501}
_HTML = re.compile(r'^\s*(?:<!doctype\s+html|<\?xml|<(?:html|head|body|script|form|title|div)\b)', re.I)


@dataclass(frozen=True)
class DeviceLogResult:
    status: str
    text: str = ''
    source: str = ''
    collected_at: str = field(default_factory=utc_now)
    kind: str = 'miner'
    truncated: bool = False


def _family(row):
    if not isinstance(row, dict):
        return None
    identity = row.get('Identity')
    identity = identity if isinstance(identity, dict) else {}
    profile = row.get('ProfileId') or identity.get('profile_id')
    if row.get('ProfileId') and identity.get('profile_id') not in (None, row['ProfileId']):
        return None
    if profile == 'bitmain.vnish':
        return 'vnish'
    if isinstance(profile, str) and (profile == 'bitmain.stock' or profile.startswith('bitmain.stock.')):
        return 'antminer'
    if profile == 'microbt.rpc3':
        return 'whatsminer'
    if profile == 'elphapex.luci':
        return 'elphapex'
    return None


def supported_log_kinds(row):
    """Known adapter kinds, not a promise that a device exposes either route."""
    family = _family(row)
    if family == 'elphapex':
        # Its Log page exposes the current miner log, not a separate kernel log.
        return ('miner',)
    return ('miner', 'system') if family else ()


def _decode(raw, *, imported=False):
    if not isinstance(raw, bytes) or len(raw) > MAX_LOG_BYTES:
        raise ProtocolError('HTTP response too large')
    if imported and raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        try:
            text = raw.decode('utf-16')
        except UnicodeError:
            raise ProtocolError('Invalid text log') from None
    else:
        text = raw.decode('utf-8-sig', errors='replace')
        if text.count('\ufffd') > max(8, len(text) // 100):
            raise ProtocolError('Invalid text log')
    if any(ord(ch) < 32 and ch not in '\t\n\r' for ch in text):
        raise ProtocolError('Binary log')
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    if _HTML.match(text):
        raise ProtocolError('Expected log text, received a web page')
    if not imported and text.lstrip().startswith(('{', '[')):
        try:
            value = json.loads(text)
        except ValueError:
            pass  # Timestamp prefixes such as [2026-10-31] are ordinary logs.
        else:
            if isinstance(value, (dict, list)):
                raise ProtocolError('Unexpected JSON log response')
    return text


def load_log_file(path):
    """Import a bounded UTF-8/UTF-16 text file, without retaining its local path."""
    source = ''
    try:
        path = Path(path)
        source = path.name
        with path.open('rb') as stream:
            raw = stream.read(MAX_LOG_BYTES + 1)
        if len(raw) > MAX_LOG_BYTES:
            return DeviceLogResult('too_large', source=source, kind='imported')
        return DeviceLogResult('succeeded', _decode(raw, imported=True), source, kind='imported')
    except (OSError, ValueError, TypeError):
        return DeviceLogResult('unavailable', source=source, kind='imported')
    except ProtocolError:
        return DeviceLogResult('invalid_response', source=source, kind='imported')


def _response(status, raw, path, kind):
    if status in (401, 403):
        raise AuthenticationError('Log access required')
    if status in _MISSING:
        return DeviceLogResult('unsupported', source=path, kind=kind)
    if status != 200:
        return DeviceLogResult('unavailable', source=path, kind=kind)
    return DeviceLogResult('succeeded', _decode(raw), path, kind=kind)


def _stock(transport, kind):
    paths = ('/cgi-bin/log.cgi', '/cgi-bin/get_kernel_log.cgi') if kind == 'miner' else ('/cgi-bin/get_kernel_log.cgi',)
    for path in paths:
        status, raw = transport.http(path, response_limit=MAX_LOG_BYTES)
        result = _response(status, raw, path, kind)
        if result.status != 'unsupported':
            return result
    return result


def _elphapex(transport, kind):
    # GET is the firmware UI's current-log read. Do not request historical logs
    # or send the POST operations accepted by this same endpoint.
    for path in ('/cgi-bin/hlog.cgi', '/cgi-bin/luci/hlog.cgi'):
        status, raw = transport.http(path, response_limit=MAX_LOG_BYTES)
        result = _response(status, raw, path, kind)
        if result.status != 'unsupported':
            return result
    return result


def _vnish(transport, kind, candidates):
    path = '/api/v1/logs/' + kind
    try:
        status, raw = transport.http(path, response_limit=MAX_LOG_BYTES)
        return _response(status, raw, path, kind)
    except AuthenticationError:
        pass
    for credentials in candidates:
        transport.operation.remaining()
        try:
            status, raw = transport.http('/api/v1/unlock', 'POST',
                payload={'pw': credentials.password}, response_limit=16 * 1024)
            if status in (401, 403):
                continue
        except AuthenticationError:
            continue
        # A server/rate-limit failure is not a reason to try more passwords.
        if status != 200:
            return DeviceLogResult('unavailable', source=path, kind=kind)
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError):
            return DeviceLogResult('invalid_response', source=path, kind=kind)
        token = payload.get('token') if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token or len(token) > 8192 or any(ord(ch) < 33 or ord(ch) > 126 for ch in token):
            return DeviceLogResult('invalid_response', source=path, kind=kind)
        # A successful unlock followed by forbidden log access is final: this
        # is a permissions problem, not a reason to cycle through passwords.
        status, raw = transport.http(path, headers={'Authorization': 'Bearer ' + token},
                                     response_limit=MAX_LOG_BYTES)
        return _response(status, raw, path, kind)
    return DeviceLogResult('auth_required', source=path, kind=kind)


def _receive_log_bytes(sock, length, operation):
    """Read one bounded segment, allowing cancellation while the miner packs logs."""
    data = bytearray()
    while len(data) < length:
        sock.settimeout(operation.timeout(0.5))
        try:
            chunk = sock.recv(min(65536, length - len(data)))
        except TimeoutError:
            operation.remaining()
            continue
        if not chunk:
            raise ProtocolError('Incomplete log download')
        data.extend(chunk)
    operation.remaining()
    return bytes(data)


def _whatsminer(transport, kind):
    """RPC header followed by a raw TGZ stream on the same TCP connection.

    get.log.download is a public read. Never supply account/token/password or
    enable the write switch. Firmware, pools and log-upload settings are untouched.
    """
    operation = transport.operation
    source = 'get.log.download/' + ('miner-state.log' if kind == 'miner' else 'system.log')
    with transport.connection(4433) as sock:
        payload = b'{"cmd":"get.log.download"}'
        operation.request_count += 1
        sock.settimeout(operation.timeout(3.0))
        sock.sendall(struct.pack('<I', len(payload)) + payload)
        header_size = struct.unpack('<I', _receive_log_bytes(sock, 4, operation))[0]
        if not 0 < header_size <= 65536:
            raise ProtocolError('Invalid log response frame')
        try:
            response = json.loads(_receive_log_bytes(sock, header_size, operation))
        except (ValueError, UnicodeError):
            raise ProtocolError('Invalid log response JSON') from None
        if (not isinstance(response, dict) or response.get('desc') != 'get.log.download'
                or type(response.get('code')) is not int):
            raise ProtocolError('Unexpected log response')
        code = response['code']
        if code != 0:
            status = {-2: 'unsupported', -4: 'auth_required'}.get(code, 'unavailable')
            return DeviceLogResult(status, source=source, kind=kind)
        message = response.get('msg')
        size = message.get('logsize') if isinstance(message, dict) else None
        if isinstance(size, str) and re.fullmatch(r'[0-9]{1,10}', size):
            size = int(size)
        if type(size) is not int or size <= 0:
            raise ProtocolError('Invalid log archive size')
        if size > MAX_ARCHIVE_BYTES:
            raise LogArchiveLimitError('Log archive is too large')
        archive = _receive_log_bytes(sock, size, operation)
    text, truncated = extract_whatsminer_log(archive, kind, operation=operation)
    return DeviceLogResult('succeeded', text, source, kind=kind, truncated=truncated)


def read_device_log(row, kind='miner', *, access_profiles=None, cancel=None, transport_factory=Transport):
    """Read one selected device with a shared deadline across all auth attempts.

    The caller provides a scanner row with a positively identified profile.
    No scan, write command, log clear, shell, redirect or arbitrary URL is used.
    Explicit empty/disabled access profiles never restore default passwords.
    """
    family = _family(row)
    if family is None or kind not in supported_log_kinds(row):
        return DeviceLogResult('unsupported', kind=kind)
    try:
        ip = str(ip_address(row.get('IP', '')))
        if ip_address(ip).version != 4:
            return DeviceLogResult('unsupported', kind=kind)
        identity = row.get('Identity')
        if isinstance(identity, dict) and identity.get('ip') not in (None, ip):
            return DeviceLogResult('unsupported', kind=kind)
    except (ValueError, TypeError):
        return DeviceLogResult('unsupported', kind=kind)
    cancel = cancel if cancel is not None else Event()
    operation = Operation(ScanOptions(connect_timeout=2.0, read_timeout=5.0,
        device_timeout=WHATSMINER_LOG_TIMEOUT_SECONDS if family == 'whatsminer' else LOG_TIMEOUT_SECONDS,
        max_response_bytes=MAX_LOG_BYTES), cancel)
    path = '/api/v1/logs/' + kind if family == 'vnish' else ('/cgi-bin/log.cgi' if kind == 'miner' else '/cgi-bin/get_kernel_log.cgi')
    if family == 'whatsminer':
        path = 'get.log.download/' + ('miner-state.log' if kind == 'miner' else 'system.log')
    elif family == 'elphapex':
        path = '/cgi-bin/hlog.cgi'
    try:
        operation.remaining()
        if family == 'whatsminer':
            with transport_factory(ip, operation, None) as transport:
                return _whatsminer(transport, kind)
        profiles = access_profiles if access_profiles is not None else AccessProfiles(standard_profiles())
        if family == 'elphapex':
            # Both verified DG1 firmwares expose current logs without auth.
            # Send credentials only if that read explicitly requires them.
            try:
                with transport_factory(ip, operation, None) as transport:
                    return _elphapex(transport, kind)
            except AuthenticationError:
                pass
        candidates = profiles.candidates(ip, family)
        if family == 'vnish':
            # VNish authenticates with its own session token. Never attach
            # HTTP Basic/Digest credentials to anonymous reads or unlock calls.
            with transport_factory(ip, operation, None) as transport:
                return _vnish(transport, kind, candidates)
        attempts = candidates if family == 'elphapex' else candidates or [None]
        for credentials in attempts:
            operation.remaining()
            try:
                with transport_factory(ip, operation, credentials) as transport:
                    return _elphapex(transport, kind) if family == 'elphapex' else _stock(transport, kind)
            except AuthenticationError:
                continue
        return DeviceLogResult('auth_required', source=path, kind=kind)
    except Cancelled:
        return DeviceLogResult('cancelled', source=path, kind=kind)
    except (DeadlineExceeded, requests.Timeout, TimeoutError):
        return DeviceLogResult('cancelled' if cancel.is_set() else 'timeout', source=path, kind=kind)
    except AuthenticationError:
        return DeviceLogResult('auth_required', source=path, kind=kind)
    except MissingLogError:
        return DeviceLogResult('unsupported', source=path, kind=kind)
    except LogArchiveLimitError:
        return DeviceLogResult('too_large', source=path, kind=kind)
    except ProtocolError as exc:
        status = 'too_large' if str(exc) == 'HTTP response too large' else 'invalid_response'
        return DeviceLogResult(status, source=path, kind=kind)
    except (OSError, requests.RequestException):
        return DeviceLogResult('cancelled' if cancel.is_set() else 'unavailable', source=path, kind=kind)
    except Exception:
        # Exceptions may contain credentials, tokens or response bodies.
        return DeviceLogResult('error', source=path, kind=kind)
