"""Profile-based command dispatch, explicit credentials and read-back verification."""
import base64
import hashlib
import json
import re
import time
from threading import BoundedSemaphore, Event
from uuid import uuid4

import requests

from .models import CommandResult
from .normalization import value_at
from .runtime import AuthenticationError, Cancelled, DeadlineExceeded, Operation, ProtocolError, ScanOptions
from .whatsminer_compatibility import WhatsminerAccessError

# User actions are independent of the device's protocol and power-mode names.
# Keep `normal` for existing callers; drivers receive only `mining_start`.
ALIASES = {"led_on": "identify_on", "led_off": "identify_off",
           "sleep": "mining_stop", "wakeup": "mining_start", "normal": "mining_start"}
_slots = BoundedSemaphore(8)


def mode_payload(config, target, model, *, pitbit=False):
    """Only known config signatures; never send conflicting mode keys."""
    if not isinstance(config, dict) or target not in (0, 1):
        raise ProtocolError("Invalid mode configuration")
    if pitbit:
        if "bitmain-work-mode" not in config:
            raise ProtocolError("PitBit mode signature missing")
        return {"bitmain-work-mode": str(target)}, "bitmain-work-mode"
    # Model alone cannot authorize a write: the configuration signature is required.
    if "bitmain-work-mode" in config and "miner-mode" in config:
        raise ProtocolError("Ambiguous mode configuration")
    if re.search(r"\b(?:L7|L9|D7|D9|Z11)\b", model, re.I):
        if not ({"miner-mode", "bitmain-work-mode"} & config.keys()):
            raise ProtocolError("Legacy mode signature missing")
        key, frequency = "miner-mode", "freq-level"
    elif re.search(r"\b(?:S19\w*|S21\w*|T19\w*|T21\w*)\b", model, re.I) and "bitmain-work-mode" in config:
        key, frequency = "bitmain-work-mode", "bitmain-freq-level"
    else:
        raise ProtocolError("No confirmed mode payload for this model/configuration")
    # A complete config write cannot preserve masked credentials safely.
    def masked(value):
        if isinstance(value, dict):
            return any(masked(v) for v in value.values())
        if isinstance(value, list):
            return any(masked(v) for v in value)
        return isinstance(value, str) and len(value) >= 3 and set(value) <= {"*", "•"}
    if masked(config):
        raise ProtocolError("Configuration contains masked fields")
    if frequency not in config:
        raise ProtocolError("Required frequency field is unavailable; refusing to invent it")
    result = dict(config)
    result.pop("miner-mode", None)
    result.pop("bitmain-work-mode", None)
    result[key] = target
    return result, key


def _http_accept(transport, path, method="POST", payload=None, headers=None, *, success_codes=(0, "0", "B000")):
    status, raw = transport.http(path, method, payload=payload, headers=headers)
    if status not in (200, 204):
        return False
    if raw:
        try:
            response = json.loads(raw)
        except (ValueError, UnicodeError):
            response = None
        if isinstance(response, dict):
            if response.get("success") is False or response.get("error"):
                return False
            if "stats" in response and response["stats"] != "success":
                return False
            code = response.get("code")
            if code is not None and code not in success_codes:
                return False
    return True


def antminer(transport, record, action, credentials):
    if credentials is None:
        raise AuthenticationError("Введите учётные данные ASIC")
    if action in ("identify_on", "identify_off"):
        accepted = _http_accept(transport, "/cgi-bin/blink.cgi", payload={"blink": action == "identify_on"})
        return accepted, None
    if action == "reboot":
        return _http_accept(transport, "/cgi-bin/reboot.cgi", "GET"), None
    if action not in ("mining_stop", "mining_start"):
        raise ProtocolError("Unsupported Antminer action")
    config = transport.http_json("/cgi-bin/get_miner_conf.cgi")
    target = 1 if action == "mining_stop" else 0
    payload, key = mode_payload(config, target, record.identity.model, pitbit=record.identity.firmware == "PitBit")
    accepted = _http_accept(transport, "/cgi-bin/set_miner_conf.cgi", payload=payload)

    def verify():
        current = transport.http_json("/cgi-bin/get_miner_conf.cgi") or {}
        # Use the same exact field that was written; do not try another key.
        return key in current and str(current[key]) == str(target)
    return accepted, verify


def vnish(transport, record, action, credentials, *, credential_candidates=None, on_authenticated=None, control_rule=None):
    if credentials is None:
        raise AuthenticationError("Введите пароль VNish")
    token = None
    candidates = list(dict.fromkeys(credential_candidates or [credentials]))[:3]
    for candidate in candidates:
        try:
            token = transport.http_json("/api/v1/unlock", "POST", payload={"pw": candidate.password})
        except AuthenticationError:
            continue
        # Do not try another password on rate limiting, malformed replies or 5xx.
        if token and token.get("token") and on_authenticated:
            on_authenticated(candidate)
        break
    if not token or not token.get("token"):
        raise AuthenticationError("VNish не выдал токен")
    headers = {"Authorization": "Bearer " + str(token["token"])}
    definitions = {
        "identify_on": ("/api/v1/find-miner", {"on": True}),
        "identify_off": ("/api/v1/find-miner", {"on": False}),
        "reboot": ("/api/v1/system/reboot", None),
        "mining_stop": ("/api/v1/mining/stop", None),
        "mining_start": ("/api/v1/mining/start", None),
    }
    if control_rule is not None:
        if (action not in ('identify_on', 'identify_off')
                or control_rule not in ({'_vnish_interface': 'locate-miner'}, {'_vnish_interface': 'find-miner'})):
            raise ProtocolError('Unknown VNish command contract')
        if control_rule['_vnish_interface'] == 'locate-miner':
            definitions[action] = ('/api/v1/locate-miner', {'is_enabled': action == 'identify_on'})
    path, payload = definitions[action]
    def verify():
        if action in ("identify_on", "identify_off"):
            data = transport.http_json("/api/v1/status", headers={**headers, "Cache-Control": "no-cache"}) or {}
            state = data.get("find_miner")
            verify.observed_state = state if type(state) is bool else None
            if record is not None:
                record.telemetry.identify_enabled = verify.observed_state
            return verify.observed_state is (action == "identify_on")
        data = transport.http_json("/api/v1/summary", headers=headers) or {}
        miner = data.get("miner", data)
        state = miner.get("miner_status", {}).get("miner_state")
        return state in ({"stopped", "paused", "sleep"} if action == "mining_stop" else {"mining"})
    if action in ('identify_on', 'identify_off'):
        # VNish 1.2.6/1.2.7 can toggle despite the JSON 'on' field. A repeated
        # locate request must never undo the target state. Read before writing.
        if verify():
            verify.already_target = True
            return True, verify
        if verify.observed_state is None:
            raise ProtocolError('VNish LED state is unavailable before write')
    accepted = _http_accept(transport, path, payload=payload, headers=headers)
    return accepted, verify if action != "reboot" else None


def whatsminer(transport, record, action, credentials):
    from .whatsminer_compatibility import resolve, info_message
    if credentials is None:
        raise WhatsminerAccessError("Добавьте профиль доступа WhatsMiner: логин super или user1–user3 и пароль этой учётной записи")
    if credentials.username not in {'super', 'user1', 'user2', 'user3'}:
        raise WhatsminerAccessError("WhatsMiner RPC 3: нужен логин super или user1–user3 и непустой пароль этой учётной записи; admin/root не подходят")
    if not credentials.password:
        raise WhatsminerAccessError("Укажите пароль учётной записи API в профиле WhatsMiner в «Доступ к ASIC». Общий профиль с адресами * применяется ко всем WhatsMiner")
    definitions = {
        "reboot": ("set.system.reboot", None),
        "mining_stop": ("set.miner.service", "stop"),
        "mining_start": ("set.miner.service", "start"),
        "identify_off": ("set.system.led", "auto"),
        "identify_on": ("set.system.led", [{"color": "red", "period": 200, "duration": 100, "start": 0}, {"color": "green", "period": 200, "duration": 150, "start": 0}]),
    }
    command, parameter = definitions[action]
    with transport.connection(4433) as sock:
        info = transport.rpc_packet(sock, {"cmd": "get.device.info"})
        compatibility = resolve(info)
        if action not in compatibility['rules']:
            raise WhatsminerAccessError(compatibility['reason'])
        msg = info_message(info)
        if msg['miner']['type'] != record.identity.model:
            raise ProtocolError("Whatsminer identity changed before write")
        if record.identity.firmware_version and msg['system'].get('fwversion') != record.identity.firmware_version:
            raise ProtocolError("Whatsminer firmware changed before write")
        salt = msg['salt']
        timestamp = int(time.time())
        source = f"{command}{credentials.password}{salt}{timestamp}".encode()
        token = base64.b64encode(hashlib.sha256(source).digest()).decode()[:8]
        packet = {"cmd": command, "ts": timestamp, "token": token, "account": credentials.username}
        if parameter is not None:
            packet['param'] = parameter
        result = transport.rpc_packet(sock, packet)
    if type(result.get('code')) is int and result['code'] == -4:
        raise WhatsminerAccessError("WhatsMiner отказал в доступе (-4): проверьте API Write, учётную запись, её пароль и разрешение на команду")
    def verify():
        info = transport.rpc("get.device.info")
        msg = info_message(info)
        if msg is None or msg['miner']['type'] != record.identity.model:
            return False
        if action in ('identify_on', 'identify_off'):
            return msg['system'].get('ledstatus') in (('manual', 'flash') if action == 'identify_on' else ('auto',))
        return str(msg['miner']['working']).lower() == ("false" if action == "mining_stop" else "true")
    accepted = type(result.get('code')) is int and result['code'] == 0 and result.get('desc') == command
    return accepted, verify if action != 'reboot' else None


def elphapex(transport, record, action, credentials):
    definitions = {
        "identify_on": ("ftm_ledtest.cgi", {"leds_blue": 1, "leds_red": 0, "leds_flash": 1, "leds_time": 0}),
        "identify_off": ("ftm_ledtest.cgi", {"leds_blue": 0, "leds_red": 0, "leds_flash": 0, "leds_time": 0}),
        "reboot": ("reboot.cgi", {}), "mining_stop": ("setworkmode.cgi", {"workmode": "-1000"}),
        "mining_start": ("setworkmode.cgi", {"workmode": "0"}),
    }
    path, payload = definitions[action]
    accepted = _http_accept(transport, "/cgi-bin/luci/" + path,
                            "GET" if action == "reboot" else "POST",
                            payload=None if action == "reboot" else payload,
                            success_codes=(0, "0", "B000", "M000"))
    def verify():
        if action in ("identify_on", "identify_off"):
            data = transport.http_json("/cgi-bin/luci/get_blink_status.cgi") or {}
            return data.get("blink") is (action == "identify_on")
        data = transport.http_json("/cgi-bin/luci/get_miner_conf.cgi") or {}
        return str(data.get("fc-work-mode")) == ("-1000" if action == "mining_stop" else "0")
    return accepted, verify if action != "reboot" else None


def jasminer(transport, record, action, credentials):
    if credentials is None:
        raise AuthenticationError("Введите учётные данные Jasminer")
    path = {"identify_on": "/cgi-bin/find_miner_on.cgi", "identify_off": "/cgi-bin/find_miner_off.cgi"}[action]
    return _http_accept(transport, path), None


def avalon(transport, record, action, credentials):
    from .avalon_compatibility import LED_ACTIONS, led_command, power_command
    if action in LED_ACTIONS:
        return led_command(transport, action)
    return power_command(transport, action)


EXECUTORS = {"antminer": antminer, "pitbit": antminer, "vnish": vnish, "whatsminer": whatsminer, "elphapex": elphapex, "jasminer": jasminer, "avalon": avalon}
SUPPORTED_ACTIONS = {
    key: {"identify_on", "identify_off", "reboot", "mining_stop", "mining_start"}
    for key in ("antminer", "pitbit", "vnish", "whatsminer", "elphapex")
}
SUPPORTED_ACTIONS["jasminer"] = {"identify_on", "identify_off"}
SUPPORTED_ACTIONS["avalon"] = {"identify_on", "identify_off", "identify_toggle", "reboot", "mining_stop", "mining_start"}


def declarative_command(transport, rule):
    payload = rule.get("payload")
    if rule.get("read_config"):
        config = transport.http_json(rule["read_config"])
        if not isinstance(config, dict) or not isinstance(payload, dict):
            raise ProtocolError("Configuration unavailable")
        def has_masked(value):
            if isinstance(value, dict):
                return any(has_masked(v) for v in value.values())
            if isinstance(value, list):
                return any(has_masked(v) for v in value)
            return isinstance(value, str) and len(value) >= 3 and set(value) <= {"*", "•"}
        if has_masked(config):
            raise ProtocolError("Configuration contains masked fields")
        mapping = rule.get("config_fields")
        if '_stock_contract' in rule:
            from .stock_compatibility import config_mapping
            if config_mapping(config, rule['_stock_contract']) != mapping:
                raise ProtocolError("Configuration schema changed after compatibility detection")
            current = value_at(config, rule['verify']['field'])
            if type(current) is not type(rule['verify']['equals']):
                raise ProtocolError("Configuration mode type changed")
        if mapping is not None:
            if any(source not in config or config[source] is None for source in mapping.values()):
                raise ProtocolError("Required configuration fields are unavailable")
            payload = {**{target: config[source] for target, source in mapping.items()}, **payload}
        else:
            payload = {**config, **payload}
    try:
        accepted = _http_accept(transport, rule["path"], rule.get("method", "POST"), payload,
                                success_codes=tuple(rule.get("success_codes", (0, "0", "B000"))))
    except (OSError, requests.RequestException):
        # Configuration writes can finish on the device after our HTTP timeout.
        # Read back the intended state, but never repeat the write.
        accepted = None
    verification = rule["verify"]
    def verify():
        data = transport.http_json(verification["path"])
        actual = value_at(data, verification["field"])
        expected = verification["equals"]
        return type(actual) is type(expected) and actual == expected
    return accepted, verify


def execute_command(service, ip, action, *, device_id=None, command_id=None, cancel=None, allow_unverified=False):
    """No write retry. Experimental compatibility must be explicitly requested."""
    command_id = command_id or str(uuid4())
    existing = service.repository.command_result(command_id)
    if existing is not None:
        return existing
    action = ALIASES.get(action, action)
    cancel = cancel if cancel is not None else Event()
    op = Operation(ScanOptions(read_timeout=10, device_timeout=30), cancel)
    record = None
    def finish(status, message, accepted=False):
        result = CommandResult(command_id, status, message, accepted, record.identity.device_id if record else device_id, record.identity.profile_id if record else None)
        service.repository.journal(result)
        return result
    slot = False
    locked = False
    lock = service.lock_for(ip)
    try:
        while not _slots.acquire(timeout=0.05):
            op.remaining()
        slot = True
        while not lock.acquire(timeout=0.05):
            op.remaining()
        locked = True
        existing = service.repository.command_result(command_id)
        if existing is not None:
            return existing
        previous = service.get_record(ip)
        if previous is None:
            return finish("skipped", "Сначала выполните идентификацию устройства")
        record = service.poll(ip, force_identify=True, cancel=cancel)
        if record is None or record.telemetry.stale:
            return finish("skipped", "Не удалось подтвердить актуальность профиля")
        if record.identity.fingerprint != previous.identity.fingerprint or (device_id and record.identity.device_id != device_id):
            names = {'model': 'модель', 'firmware_version': 'версия прошивки',
                     'api_version': 'версия API', 'serial': 'серийный номер',
                     'mac': 'MAC-адрес', 'profile_id': 'профиль устройства',
                     'profile_version': 'версия профиля'}
            changed = [label for field, label in names.items()
                       if getattr(record.identity, field) != getattr(previous.identity, field)]
            if record.identity.fingerprint != previous.identity.fingerprint and not changed:
                changed.append('метаданные идентификации прошивки')
            if device_id and record.identity.device_id != device_id:
                changed.append('идентификатор выбранной строки')
            return finish("skipped", "Проверка перед командой: изменились " + ', '.join(changed)
                          + ". Запрос управления не отправлен. Проверьте актуальные данные устройства.")
        profile = service.registry.by_id[record.identity.profile_id]
        compatibility = service._control_contracts.get(ip, {})
        if action in compatibility.get('blocked', ()):
            return finish("unsupported", compatibility.get('reason', "Совместимость команды не подтверждена для текущего интерфейса или конфигурации ASIC"))
        compatible = action in compatibility.get('rules', {})
        rule = compatibility['rules'][action] if compatible else (profile.command_rules or {}).get(action)
        if rule is None and action not in SUPPORTED_ACTIONS.get(profile.control, set()):
            return finish("unsupported", "Для этого профиля действие не реализовано")
        if not compatible and action not in profile.verified_commands and not allow_unverified:
            return finish("unsupported", "Не подтверждена совместимость API для этой команды; нужен поддерживаемый интерфейс или проверенный профиль")
        op.remaining()
        # Persist uncertain intent before any write. A crash must never cause replay.
        finish("unconfirmed", "Выполнение начато; конечный результат пока неизвестен")
        with service.transport_factory(ip, op, service.credentials_for(ip)) as transport:
            if profile.control == 'vnish':
                accepted, verify = vnish(transport, record, action, service.credentials_for(ip),
                                         credential_candidates=service.credential_candidates(ip, 'vnish'),
                                         on_authenticated=lambda credentials: service.remember_credentials(ip, credentials),
                                         control_rule=rule)
            elif rule is not None:
                accepted, verify = declarative_command(transport, rule)
            else:
                accepted, verify = EXECUTORS[profile.control](transport, record, action, service.credentials_for(ip))
            if accepted is False:
                return finish("failed", "API отклонил команду")
            if verify is None:
                return finish("unconfirmed", "API принял команду; состояние не подтверждено", True)
            for _ in range(3):
                op.pause(1)
                try:
                    if verify():
                        if getattr(verify, 'already_target', False):
                            return finish("succeeded", "Подсветка уже в нужном состоянии; команда не отправлялась")
                        message = ("Ожидаемое состояние подтверждено чтением API" if accepted else
                                   "Ответ на запись не получен; целевое состояние подтверждено чтением API")
                        return finish("succeeded", message, bool(accepted))
                except (OSError, requests.RequestException, ProtocolError):
                    continue
            message = ("API принял команду, но ожидаемое состояние не подтверждено" if accepted else
                       "Ответ на запись не получен, состояние не подтверждено; команда не повторяется")
            if profile.control == 'vnish' and action in ('identify_on', 'identify_off'):
                state = getattr(verify, 'observed_state', None)
                observed = 'включена' if state is True else 'выключена' if state is False else 'неизвестно'
                expected = 'включена' if action == 'identify_on' else 'выключена'
                endpoint = (rule or {}).get('_vnish_interface', 'find-miner')
                message += f"; подсветка по API: {observed}, ожидалось: {expected} (VNish {endpoint})"
            return finish("unconfirmed", message, bool(accepted))
    except Cancelled:
        return finish("unconfirmed" if service.repository.command_result(command_id) else "cancelled", "Операция остановлена; отправленная команда не повторяется")
    except WhatsminerAccessError as exc:
        return finish("failed", str(exc))
    except AuthenticationError:
        return finish("failed", "Требуются корректные учётные данные и разрешение записи API")
    except (OSError, requests.RequestException, DeadlineExceeded):
        return finish("unconfirmed", "Связь прервана или истёк срок ожидания; команда не повторяется")
    except (ProtocolError, KeyError, ValueError):
        return finish("failed", "Профиль или ответ не соответствует требуемому формату")
    finally:
        if locked:
            lock.release()
        if slot:
            _slots.release()
