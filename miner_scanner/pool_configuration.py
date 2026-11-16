"""Preview-bound pool edits with stable worker names and one write per device.

This API deliberately does not add arguments to the existing control API used
by Android and the agent. Configurations/passwords live only in private plan
state; public previews and command journals contain no password values.
"""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from copy import deepcopy
from dataclasses import dataclass, field, replace
from hashlib import sha256
from ipaddress import IPv4Address
import json
import time
from threading import Event
from urllib.parse import urlsplit
from uuid import uuid4

import requests

from .models import CommandResult
from .pools import pool_columns
from .pool_evidence import identity_evidence, evidence_from_state, sanitize_evidence
from .runtime import AuthenticationError, Cancelled, DeadlineExceeded, Operation, ProtocolError
from . import pool_backends as backends


@dataclass(frozen=True)
class WorkerNaming:
    """The name before the dot is common; only the suffix is device-specific."""
    name: str | None
    mode: str = 'index'
    start: int = 1
    width: int = 1
    _offset: int = field(default=0, repr=False, compare=False)

    def __post_init__(self):
        if self.name is not None:
            _text(self.name, 'Общее имя worker', allow_empty=False)
        if self.name is not None and '.' in self.name:
            raise ValueError('Общее имя worker указывается до точки и не должно содержать точку')
        if self.mode not in {'index', 'ip', 'last_octet'}:
            raise ValueError('Неизвестный способ формирования worker')
        if type(self.start) is not int or not 1 <= self.start <= 1_000_000_000:
            raise ValueError('Начальный номер должен быть от 1 до 1000000000')
        if type(self.width) is not int or not 1 <= self.width <= 9:
            raise ValueError('Разрядность должна быть от 1 до 9')

    def render(self, ip, index, *, existing=None):
        address = str(IPv4Address(ip))
        name = self.name
        if name is None:
            if not isinstance(existing, str) or not existing:
                raise ValueError('Не получено текущее общее имя worker до точки')
            name = existing.split('.', 1)[0]
            _text(name, 'Текущее общее имя worker', allow_empty=False)
        suffix = (str(self.start + index + self._offset).zfill(self.width) if self.mode == 'index'
                  else address.replace('.', '_') if self.mode == 'ip'
                  else address.rsplit('.', 1)[1])
        return name + '.' + suffix


@dataclass(frozen=True)
class PoolEdit:
    """None preserves a field; an explicit empty string requests clearing it."""
    slot: int
    url: str | None = None
    worker: WorkerNaming | str | None = None
    password: str | None = field(default=None, repr=False)

    def __post_init__(self):
        if type(self.slot) is not int or not 0 <= self.slot < 3:
            raise ValueError('Можно изменять только слоты пулов 1–3')
        if self.url is not None:
            _pool_url(self.url)
        if self.worker is not None and not isinstance(self.worker, WorkerNaming):
            _text(self.worker, 'Worker')
        if self.password is not None:
            if not isinstance(self.password, str) or len(self.password) > 4096 or any(
                    ord(c) < 32 or ord(c) == 127 for c in self.password):
                raise ValueError('Недопустимый пароль пула')


@dataclass(frozen=True)
class PoolChange:
    slot: int
    field: str
    before: str | None
    after: str | None


@dataclass(frozen=True)
class _Prepared:
    state: object = field(repr=False)
    expected: str = field(repr=False)
    fingerprint: str = field(repr=False)
    signature: str = field(repr=False)
    seal: str = field(repr=False)
    explicit_password_slots: tuple[int, ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class PoolPlan:
    ip: str
    device_id: str | None = None
    profile_id: str | None = None
    model: str = ''
    firmware: str = ''
    firmware_version: str | None = None
    status: str = 'failed'
    message: str = ''
    changes: tuple[PoolChange, ...] = ()
    command_id: str = field(default_factory=lambda: str(uuid4()))
    _prepared: _Prepared | None = field(default=None, repr=False, compare=False)
    _worker_names: tuple = field(default=(), repr=False, compare=False)
    api_version: str | None = None
    evidence: dict = field(default_factory=dict, repr=False, compare=False)


def _text(value, label, *, allow_empty=True):
    if (not isinstance(value, str) or len(value) > 1024
            or (not allow_empty and not value) or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError(label + ': недопустимое значение')


def _pool_url(value):
    _text(value, 'Адрес пула')
    if not value:
        return
    try:
        parts = urlsplit(value if '://' in value else '//' + value)
        if not parts.hostname or parts.username is not None or parts.password is not None or '@' in parts.netloc:
            raise ValueError
        if parts.scheme and parts.scheme.lower() not in {'stratum+tcp', 'stratum+ssl', 'stratum+tls', 'stratum', 'tcp', 'ssl', 'tls'}:
            raise ValueError
        _ = parts.port
    except ValueError:
        raise ValueError('Недопустимый адрес пула; логин и пароль задаются отдельными полями') from None


def _masked(value):
    return isinstance(value, str) and bool(value) and set(value) <= {'*', '•'}


def _preview_value(key, value):
    if key != 'url' or value is None:
        return value
    # Configuration can contain legacy URL-embedded credentials. Preserve the
    # private value for an exact write, but use the existing reviewed display
    # projection for previews as well as for the main table.
    return pool_columns({'pools': {'POOLS': [{'url': value}]}}, 'generic')['Pool']


def _acquire(lock, cancel):
    while not lock.acquire(timeout=.05):
        if cancel.is_set():
            raise Cancelled('Cancelled')
    if cancel.is_set():
        lock.release()
        raise Cancelled('Cancelled')


def _rows(targets):
    unique = {}
    for target in targets:
        row = deepcopy(target)
        if not isinstance(row, dict) or not isinstance(row.get('IP'), str):
            raise ValueError('У каждого выбранного устройства должен быть IP')
        ip = str(IPv4Address(row['IP']))
        if ip in unique and unique[ip].get('DeviceId') != row.get('DeviceId'):
            raise ValueError('Одному IP соответствуют разные устройства; обновите выбор')
        row['IP'] = ip
        unique.setdefault(ip, row)
    return sorted(unique.values(), key=lambda row: IPv4Address(row['IP']))


def _resolved_edits(rows, edits):
    edits = tuple(edits)
    if not edits or any(not isinstance(e, PoolEdit) for e in edits):
        raise ValueError('Выберите изменяемые поля пулов')
    if len({e.slot for e in edits}) != len(edits):
        raise ValueError('Повторное изменение одного слота пула')
    if not any(e.url is not None or e.worker is not None or e.password is not None for e in edits):
        raise ValueError('Выберите хотя бы одно изменяемое поле')
    seen = {}
    resolved = {}
    for index, row in enumerate(rows):
        per_device = []
        for edit in edits:
            if isinstance(edit.worker, WorkerNaming) and edit.worker.name is None:
                worker = replace(edit.worker, _offset=index)
            else:
                worker = edit.worker.render(row['IP'], index) if isinstance(edit.worker, WorkerNaming) else edit.worker
            if isinstance(worker, str) and worker:
                key = (edit.slot, worker)
                if key in seen and seen[key] != row['IP']:
                    raise ValueError('Совпадающие workers у разных ASIC; выберите полный IP или нумерацию')
                seen[key] = row['IP']
            per_device.append(replace(edit, worker=worker))
        resolved[row['IP']] = tuple(per_device)
    return resolved


def _plan_for(row):
    return PoolPlan(row['IP'], row.get('DeviceId'), row.get('ProfileId'),
                    str(row.get('Model') or ''), str(row.get('Firmware') or ''), row.get('FirmwareVersion'),
                    api_version=row.get('ApiVersion'), evidence={'identity': identity_evidence(row=row)})


def _requested_changes(edits):
    """Show known desired values even when a device cannot supply its config."""
    changes = []
    for edit in edits:
        for key, value in (('url', edit.url), ('user', edit.worker), ('pass', edit.password)):
            if value is None:
                continue
            proposed = (None if isinstance(value, WorkerNaming) else '<changed>' if key == 'pass'
                        else _preview_value(key, value))
            changes.append(PoolChange(edit.slot, key, None, proposed))
    return tuple(changes)


def _record(service, row, op):
    previous = service.get_record(row['IP'])
    if previous is None:
        raise backends.PoolBackendError('Сначала выполните сканирование устройства', status='changed')
    current = service.poll(row['IP'], force_identify=True, cancel=op.cancel,
                           options=replace(service.options, device_timeout=min(20., op.remaining())))
    if current is None or current.telemetry.stale:
        raise backends.PoolBackendError('Нет актуального ответа устройства', status='offline')
    identity = row.get('Identity')
    expected = identity.get('fingerprint') if isinstance(identity, dict) else None
    if ((row.get('DeviceId') and row['DeviceId'] != current.identity.device_id)
            or previous.identity.fingerprint != current.identity.fingerprint
            or (expected and expected != current.identity.fingerprint)
            or (row.get('ProfileId') and row['ProfileId'] != current.identity.profile_id)):
        raise backends.PoolBackendError('Устройство или прошивка изменились; обновите предпросмотр', status='changed')
    return current


def _patch(state, edits, ip):
    slots = deepcopy(state.slots)
    changes = []
    worker_changed = False
    explicit_passwords = {edit.slot for edit in edits if edit.password is not None}
    for edit in edits:
        while len(slots) <= edit.slot:
            slots.append({})
        item = slots[edit.slot]
        try:
            worker = edit.worker.render(ip, 0, existing=item.get('user')) if isinstance(edit.worker, WorkerNaming) else edit.worker
        except ValueError:
            raise backends.PoolBackendError('Не получено текущее общее имя worker до точки') from None
        for key, value in (('url', edit.url), ('user', worker), ('pass', edit.password)):
            if value is None:
                continue
            previous = item.get(key)
            # A masked password is never equal to an explicitly supplied secret.
            if previous == value and not (key == 'pass' and _masked(previous)):
                continue
            item[key] = value
            worker_changed |= key == 'user'
            changes.append(PoolChange(edit.slot, key,
                                      '<hidden>' if key == 'pass' and previous is not None else _preview_value(key, previous),
                                      '<changed>' if key == 'pass' else _preview_value(key, value)))
    if not changes:
        return slots, ()
    if worker_changed and state.opaque.get('worker_auto_suffix'):
        raise backends.PoolBackendError('Прошивка автоматически дописывает IP к worker; точное итоговое имя не подтверждено')
    # Every enabled slot is sent by whole-list APIs. Missing and masked fields
    # must not silently become empty values, including slots outside the edit.
    for position, item in enumerate(slots):
        explicit_password = position in explicit_passwords
        if not item.get('url') and not item.get('user'):
            if any(_masked(v) for key, v in item.items()
                   if not (key == 'pass' and explicit_password)):
                raise backends.PoolBackendError('Скрытые поля конфигурации нельзя сохранить без подтверждённого контракта')
            continue
        if any(key not in item or not isinstance(item[key], str) for key in ('url', 'user', 'pass')):
            if state.backend_id == 'whatsminer.rpc3.pools' and 'pass' not in item:
                raise backends.PoolBackendError('WhatsMiner не возвращает пароли пулов; задайте пароль явно для каждого из трёх слотов')
            raise backends.PoolBackendError('Не получены все поля пулов; укажите недостающие значения явно')
        if any(_masked(v) for key, v in item.items()
               if not (key == 'pass' and explicit_password)):
            raise backends.PoolBackendError('Конфигурация содержит скрытые значения; их нельзя отправлять обратно')
    return slots, tuple(changes)


def _seal(plan, signature, expected, fingerprint, explicit_password_slots=()):
    return sha256(json.dumps([plan.ip, plan.device_id, plan.profile_id, plan.command_id,
                              signature, expected, fingerprint, explicit_password_slots],
                             ensure_ascii=False).encode()).hexdigest()


def _failure(exc):
    if isinstance(exc, Cancelled):
        return 'cancelled', 'Операция отменена; запись не выполнялась'
    if isinstance(exc, AuthenticationError):
        return 'auth_required', 'Нужны учётные данные ASIC и разрешение записи API'
    if isinstance(exc, backends.PoolBackendError):
        return exc.status, str(exc)
    if isinstance(exc, (OSError, requests.RequestException, DeadlineExceeded)):
        return 'offline', 'Нет связи с устройством или истёк срок ожидания'
    if isinstance(exc, (ProtocolError, ValueError, KeyError, TypeError)):
        return 'unsupported', 'Не подтверждён формат конфигурации или интерфейс записи пулов'
    return 'failed', 'Ошибка подготовки настройки пулов'


def _prepare_one(service, row, edits, cancel):
    plan = replace(_plan_for(row), changes=_requested_changes(edits))
    guard = service.command_lock_for(plan.ip)
    device_lock = service.lock_for(plan.ip)
    acquired = locked = False
    credential_secrets = ()
    try:
        _acquire(guard, cancel)
        acquired = True
        _acquire(device_lock, cancel)
        locked = True
        op = Operation(replace(service.options, read_timeout=5., device_timeout=30.), cancel)
        record = _record(service, row, op)
        credentials = service.credentials_for(plan.ip)
        credential_secrets = (credentials.username, credentials.password) if credentials is not None else ()
        plan = replace(plan, device_id=record.identity.device_id, profile_id=record.identity.profile_id,
                       model=record.identity.model, firmware=record.identity.firmware,
                       firmware_version=record.identity.firmware_version, api_version=record.identity.api_version,
                       evidence={'identity': identity_evidence(record, secrets=credential_secrets)})
        with service.transport_factory(plan.ip, op, credentials) as transport:
            state = backends.read_state(service, record, transport)
        resolved = service.credentials_for(plan.ip)
        if resolved is not None:
            credential_secrets += (resolved.username, resolved.password)
        plan = replace(plan, evidence=evidence_from_state(state, record, secrets=credential_secrets))
        slots, changes = _patch(state, edits, plan.ip)
        plan = replace(plan, _worker_names=tuple((e.slot, slots[e.slot].get('user')) for e in edits if e.worker is not None))
        if not changes:
            return replace(plan, status='unchanged', message='Настройки уже соответствуют заданным; запись не требуется', changes=())
        explicit_password_slots = tuple(sorted({edit.slot for edit in edits if edit.password is not None}))
        backends.validate_state(state, slots, explicit_password_slots=explicit_password_slots)
        expected = json.dumps(slots, sort_keys=True, ensure_ascii=False)
        signature = backends.state_signature(state)
        fingerprint = record.identity.fingerprint
        prepared = _Prepared(state, expected, fingerprint, signature,
                             _seal(plan, signature, expected, fingerprint, explicit_password_slots),
                             explicit_password_slots)
        return replace(plan, status='ready', message='Изменения подготовлены; команда ещё не отправлялась',
                       changes=changes, _prepared=prepared)
    except Exception as exc:
        status, message = _failure(exc)
        safe = getattr(exc, 'pool_evidence', None)
        if isinstance(safe, dict):
            plan = replace(plan, evidence=sanitize_evidence({**plan.evidence, **safe}, secrets=credential_secrets))
        return replace(plan, status=status, message=message)
    finally:
        if locked:
            device_lock.release()
        if acquired:
            guard.release()


def _parallel(items, function, *, workers, cancel):
    if type(workers) is not int or not 1 <= workers <= 32:
        raise ValueError('Число параллельных операций должно быть от 1 до 32')
    iterator = iter(items)
    pending = {}
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='asic-pools')
    try:
        exhausted = False
        while pending or not exhausted:
            while not exhausted and len(pending) < workers:
                try:
                    item = next(iterator)
                except StopIteration:
                    exhausted = True
                    break
                pending[pool.submit(function, item)] = item
            if pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    pending.pop(future)
                    yield future.result()
    finally:
        if pending:
            cancel.set()
        pool.shutdown(wait=True)


def prepare_pool_batch(service, targets, edits, *, cancel=None, workers=8):
    """Read-only preview. Names are assigned before network work, then frozen."""
    cancel = cancel if cancel is not None else Event()
    edits = tuple(edits)
    rows = _rows(targets)
    resolved = _resolved_edits(rows, edits)
    prepared = _parallel(rows, lambda row: _prepare_one(service, row, resolved[row['IP']], cancel),
                         workers=workers, cancel=cancel)
    if not any(isinstance(e.worker, WorkerNaming) and e.worker.name is None for e in edits):
        yield from prepared
        return
    # Suffix-only edits resolve their name from a fresh read, so consistency
    # across the batch can only be checked after all read-only reviews finish.
    plans = list(prepared)
    bases, names = {}, {}
    problem = None
    for plan in plans:
        if plan.status not in {'ready', 'unchanged'}:
            continue
        for slot, name in plan._worker_names:
            if not name:
                continue
            base = name.split('.', 1)[0]
            if slot in bases and bases[slot] != base:
                problem = 'Общие имена до точки различаются; задайте единое общее имя worker'
            bases[slot] = base
            key = (slot, name)
            if key in names and names[key] != plan.ip:
                problem = 'Совпадающие workers у разных ASIC; выберите полный IP или нумерацию'
            names[key] = plan.ip
    for plan in plans:
        if problem is not None and plan.status == 'ready':
            plan = replace(plan, status='unsupported', message=problem, _prepared=None)
        yield plan


def _collect_one(service, row, cancel):
    """A separately requested read never creates an executable plan or journal."""
    from .pool_evidence_readers import read_evidence

    plan = _plan_for(row)
    guard, device_lock = service.command_lock_for(plan.ip), service.lock_for(plan.ip)
    acquired = locked = False
    credential_secrets = ()
    try:
        _acquire(guard, cancel)
        acquired = True
        _acquire(device_lock, cancel)
        locked = True
        op = Operation(replace(service.options, read_timeout=5., device_timeout=30.), cancel)
        record = _record(service, row, op)
        credentials = service.credentials_for(plan.ip)
        credential_secrets = (credentials.username, credentials.password) if credentials is not None else ()
        plan = replace(plan, device_id=record.identity.device_id, profile_id=record.identity.profile_id,
                       model=record.identity.model, firmware=record.identity.firmware,
                       firmware_version=record.identity.firmware_version, api_version=record.identity.api_version,
                       evidence={'identity': identity_evidence(record, secrets=credential_secrets)})
        with service.transport_factory(plan.ip, op, credentials) as transport:
            evidence = read_evidence(service, record, transport)
        resolved = service.credentials_for(plan.ip)
        if resolved is not None:
            credential_secrets += (resolved.username, resolved.password)
        return replace(plan, status='observed', evidence=sanitize_evidence(evidence, secrets=credential_secrets),
                       message='Сведения прочитаны без изменения настроек; совместимость записи не проверялась')
    except Exception as exc:
        status, message = _failure(exc)
        if isinstance(exc, (ProtocolError, ValueError, KeyError, TypeError)):
            message = 'Не подтверждён формат ответа чтения сведений о пулах'
        safe = getattr(exc, 'pool_evidence', None)
        if isinstance(safe, dict):
            plan = replace(plan, evidence=sanitize_evidence({**plan.evidence, **safe}, secrets=credential_secrets))
        return replace(plan, status=status, message=message)
    finally:
        if locked:
            device_lock.release()
        if acquired:
            guard.release()


def collect_pool_evidence_batch(service, targets, *, cancel=None, workers=8):
    """Explicitly collect read evidence without requiring any proposed changes."""
    cancel = cancel if cancel is not None else Event()
    yield from _parallel(_rows(targets), lambda row: _collect_one(service, row, cancel),
                         workers=workers, cancel=cancel)


def _execute_one(service, plan, cancel):
    guard = service.command_lock_for(plan.ip)
    device_lock = service.lock_for(plan.ip)
    acquired = locked = attempted = False
    accepted = None

    def finish(status, message):
        result = CommandResult(plan.command_id, status, message, accepted is True, plan.device_id, plan.profile_id)
        # Cancellation while waiting for another consumer must not overwrite
        # that consumer's persisted command intent or final result.
        if not acquired:
            return plan.ip, result
        try:
            service.repository.journal(result)
        except Exception:
            result = CommandResult(plan.command_id, 'unconfirmed' if attempted else 'failed',
                                   'Журнал команд недоступен; автоматический повтор запрещён',
                                   accepted is True, plan.device_id, plan.profile_id)
        return plan.ip, result

    try:
        _acquire(guard, cancel)
        acquired = True
        # A concurrent consumer must wait for the current writer's final result,
        # rather than returning its temporary persisted intent as a final result.
        existing = service.repository.command_result(plan.command_id)
        if existing is not None:
            return plan.ip, existing
        if plan.status != 'ready' or plan._prepared is None:
            return finish('skipped', 'Нет подготовленных изменений; запись не выполнялась')
        _acquire(device_lock, cancel)
        locked = True
        op = Operation(replace(service.options, read_timeout=5., device_timeout=30.), cancel)
        prepared = plan._prepared
        signature = backends.state_signature(prepared.state)
        if (signature != prepared.signature or prepared.seal != _seal(
                plan, signature, prepared.expected, prepared.fingerprint, prepared.explicit_password_slots)):
            return finish('skipped', 'Подготовленные данные изменились; обновите предпросмотр')
        row = {'IP': plan.ip, 'DeviceId': plan.device_id, 'ProfileId': plan.profile_id,
               'Identity': {'fingerprint': prepared.fingerprint}}
        record = _record(service, row, op)
        with service.transport_factory(plan.ip, op, service.credentials_for(plan.ip)) as transport:
            before = backends.read_state(service, record, transport)
            if backends.state_signature(before) != prepared.signature:
                return finish('skipped', 'Конфигурация изменилась после предпросмотра; перечитайте настройки')
            expected = json.loads(prepared.expected)
            op.remaining()
            # Journal intent before calling a setter. A crash or duplicate plan
            # can never turn an uncertain operation into automatic replay.
            service.repository.journal(CommandResult(plan.command_id, 'unconfirmed',
                'Запись начата; результат ещё не подтверждён', False, plan.device_id, plan.profile_id))
            attempted = True
            try:
                accepted = backends.write_state(service, record, transport, before, expected,
                    explicit_password_slots=prepared.explicit_password_slots)
            except (OSError, requests.RequestException, DeadlineExceeded):
                accepted = None
            if accepted is False:
                return finish('failed', 'API отклонил изменение пулов')
            op.started = time.monotonic()
            for attempt in range(3):
                if attempt:
                    op.pause(attempt)
                try:
                    op.remaining()
                    after = backends.read_state(service, record, transport)
                    runtime_only = before.opaque.get('verification_kind') == 'runtime_without_passwords'
                    expected_readback = ([{key: item[key] for key in ('url', 'user')} for item in expected]
                                         if runtime_only else expected)
                    if (not backends.masked_pool_fields(after.slots)
                            and after.slots == expected_readback
                            and backends.equivalent_state(before, after, ignore_pools=True)):
                        record.display.update(pool_columns({'pools': {'POOLS': after.slots}}, 'generic'))
                        service.repository.save(record)
                        if runtime_only:
                            return finish('unconfirmed', 'URL и worker видны в работе; API не подтверждает сохранение паролей пулов; команда не повторяется')
                        flags = before.opaque
                        if flags.get('restart_required') or flags.get('reboot_required'):
                            return finish('unconfirmed', 'Настройки сохранены и прочитаны; API требует перезапуск для применения')
                        return finish('succeeded', 'Настройки пулов подтверждены чтением конфигурации API')
                except (OSError, requests.RequestException, ProtocolError, DeadlineExceeded, backends.PoolBackendError):
                    continue
            return finish('unconfirmed', 'Запись не подтверждена чтением конфигурации; команда не повторяется')
    except Exception as exc:
        if attempted:
            return finish('unconfirmed', 'После начала записи результат не подтверждён; команда не повторяется')
        status, message = _failure(exc)
        return finish('cancelled' if status == 'cancelled' else 'unsupported' if status == 'unsupported'
                      else 'skipped' if status == 'changed' else 'failed', message)
    finally:
        if locked:
            device_lock.release()
        if acquired:
            guard.release()


def execute_pool_batch(service, plans, *, cancel=None, workers=8):
    """Apply only frozen previews; duplicates share one persisted command id."""
    cancel = cancel if cancel is not None else Event()
    unique = {}
    for plan in plans:
        if not isinstance(plan, PoolPlan):
            raise ValueError('Ожидался результат предварительного просмотра')
        if plan.ip in unique and unique[plan.ip].command_id != plan.command_id:
            raise ValueError('Для одного ASIC выбрано несколько разных изменений')
        unique.setdefault(plan.ip, plan)
    yield from _parallel(unique.values(), lambda plan: _execute_one(service, plan, cancel), workers=workers, cancel=cancel)
