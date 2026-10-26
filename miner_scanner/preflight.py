"""Read-only compatibility review. A ready result never authorizes a stale write."""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from copy import deepcopy
from dataclasses import dataclass
from ipaddress import IPv4Address
from threading import Event

import requests

from .commands import ALIASES, MAX_COMMAND_WORKERS, command_support
from .runtime import AuthenticationError, Cancelled, DeadlineExceeded


@dataclass(frozen=True)
class PreflightResult:
    ip: str
    status: str
    message: str
    target: dict
    record: dict | None = None

    @property
    def ready(self):
        return self.status == 'ready'


def is_retry_safe(result):
    """Only explicit no-write outcomes may enter a new reviewed batch.

    A generic failed result may follow a write. Missing acceptance alone is not
    evidence that replay is safe, including transport and protocol exceptions.
    """
    return (not result.accepted_by_api and
            (result.status in ('cancelled', 'skipped') or
             (result.status == 'failed' and result.message == 'API отклонил команду')))


def _credential_problem(service, record, profile, rule):
    credentials = service.credentials_for(record.identity.ip)
    # Declarative legacy HTTP contracts can legitimately work without an RPC
    # account. Only impose the prerequisites of the executor actually used.
    if rule is not None and profile.control != 'vnish':
        return None
    if profile.control == 'whatsminer':
        if (credentials is None or credentials.username not in {'super', 'user1', 'user2', 'user3'}
                or not credentials.password):
            return 'Настройте учётную запись WhatsMiner API (super или user1–user3) и её пароль.'
    elif profile.control == 'vnish':
        candidates = service.credential_candidates(record.identity.ip, 'vnish') or [credentials]
        if not any(candidate is not None and candidate.password for candidate in candidates):
            return 'Настройте пароль VNish в профилях доступа к ASIC.'
    elif profile.control in ('antminer', 'pitbit', 'jasminer') and credentials is None:
        return 'Настройте учётные данные ASIC в профилях доступа.'
    return None


def _check_one(service, target, action, cancel, experimental_ids):
    ip = str(target.get('IP', ''))
    observed = None

    def result(status, message):
        return PreflightResult(ip, status, message, target, observed)

    try:
        ip = str(IPv4Address(ip))
        if cancel.is_set():
            return result('cancelled', 'Проверка отменена. Команда не отправлялась.')
        previous = service.get_record(ip)
        selected_identity = target.get('Identity')
        selected_identity = selected_identity if isinstance(selected_identity, dict) else {}
        expected_id = target.get('DeviceId') or selected_identity.get('device_id')
        expected_fingerprint = selected_identity.get('fingerprint')
        if previous is not None:
            expected_id = expected_id or previous.identity.device_id
            expected_fingerprint = expected_fingerprint or previous.identity.fingerprint
        current = service.poll(ip, force_identify=True, cancel=cancel)
        if cancel.is_set():
            return result('cancelled', 'Проверка отменена. Команда не отправлялась.')
        if current is not None:
            observed = current.to_legacy()
        error = str(current.display.get('Error', '')) if current is not None else ''
        if error == 'AUTH REQUIRED' or (current is None and service._last_auth_failed.get(ip)):
            return result('auth_required', 'API требует авторизацию. Проверьте профили доступа к ASIC.')
        if current is None or current.telemetry.stale:
            if error in ('PROFILE CHANGED', 'AMBIGUOUS PROFILE'):
                return result('changed', 'Профиль устройства изменился или не определён однозначно. Обновите сканирование.')
            if error == 'NO TELEMETRY':
                return result('unknown', 'Не удалось получить актуальную телеметрию устройства.')
            return result('offline', 'Свежий ответ ASIC не получен. Проверьте доступность устройства и API.')
        if not expected_id and not expected_fingerprint:
            return result('unknown', 'Нет исходного паспорта выбранного устройства. Обновите сканирование.')
        if ((expected_id and current.identity.device_id != expected_id)
                or (expected_fingerprint and current.identity.fingerprint != expected_fingerprint)):
            return result('changed', 'Устройство или прошивка изменились после выбора. Обновите сканирование.')
        profile = service.registry.by_id.get(current.identity.profile_id)
        if profile is None:
            return result('unknown', 'Профиль устройства отсутствует в текущем каталоге.')
        compatibility = service._control_contracts.get(ip, {})
        supported, rule, reason = command_support(profile, compatibility, action,
            allow_unverified=current.identity.device_id in experimental_ids)
        if not supported:
            return result('unsupported', reason)
        problem = _credential_problem(service, current, profile, rule)
        if problem:
            return result('auth_required', problem)
        if action not in compatibility.get('rules', {}) and action not in profile.verified_commands:
            return result('ready', 'Экспериментальное управление разрешено для выбранного ASIC; совместимость команды не подтверждена.')
        return result('ready', 'Совместимость подтверждена чтением API. Право записи проверяется при отправке.')
    except Cancelled:
        return result('cancelled', 'Проверка отменена. Команда не отправлялась.')
    except AuthenticationError:
        return result('auth_required', 'API требует авторизацию. Проверьте профили доступа к ASIC.')
    except (OSError, requests.RequestException, DeadlineExceeded):
        return result('offline', 'Свежий ответ ASIC не получен. Проверьте доступность устройства и API.')
    except Exception as exc:
        # Raw transport/exception text can include credentials; expose a fixed
        # local explanation and its type, never the remote response itself.
        return result('failed', 'Проверка не завершена (' + type(exc).__name__ + '). Команда не отправлялась.')


def preflight_batch(service, targets, action, *, workers=32, cancel=None, experimental_ids=()):
    """Yield one read-only review per unique selected IP with bounded workers.

    The original target identity is retained even though polling updates the
    service cache. Dispatch must still validate the selected identity again.
    """
    if type(workers) is not int or not 1 <= workers <= MAX_COMMAND_WORKERS:
        raise ValueError('Command workers must be between 1 and 32')
    cancel = cancel if cancel is not None else Event()
    experimental_ids = frozenset(experimental_ids)
    rows = {}
    for row in targets:
        ip = str(row.get('IP', ''))
        rows.setdefault(ip, deepcopy(row))
    iterator = iter(rows.values())
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='asic-preflight')
    pending = {}
    exhausted = False
    try:
        while pending or not exhausted:
            while not exhausted and len(pending) < workers:
                try:
                    row = next(iterator)
                except StopIteration:
                    exhausted = True
                    break
                if cancel.is_set():
                    yield PreflightResult(str(row.get('IP', '')), 'cancelled',
                        'Проверка отменена. Команда не отправлялась.', row)
                else:
                    future = pool.submit(_check_one, service, row, ALIASES.get(action, action), cancel, experimental_ids)
                    pending[future] = row
            if not pending:
                continue
            completed, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in completed:
                row = pending.pop(future)
                try:
                    yield future.result()
                except Exception as exc:
                    yield PreflightResult(str(row.get('IP', '')), 'failed',
                        'Проверка не завершена (' + type(exc).__name__ + '). Команда не отправлялась.', row)
    finally:
        if pending:
            cancel.set()
        pool.shutdown(wait=True)
