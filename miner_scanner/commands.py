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

ALIASES = {"led_on": "identify_on", "led_off": "identify_off", "sleep": "mining_stop", "normal": "mining_start"}
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


def _http_accept(transport, path, method="POST", payload=None, headers=None):
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
            code = response.get("code")
            if code is not None and code not in (0, "0", "B000"):
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


def vnish(transport, record, action, credentials):
    if credentials is None:
        raise AuthenticationError("Введите пароль VNish")
    token = transport.http_json("/api/v1/unlock", "POST", payload={"pw": credentials.password})
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
    path, payload = definitions[action]
    accepted = _http_accept(transport, path, payload=payload, headers=headers)
    def verify():
        data = transport.http_json("/api/v1/summary") or {}
        miner = data.get("miner", data)
        state = miner.get("miner_status", {}).get("miner_state")
        return state in ({"stopped", "paused", "sleep"} if action == "mining_stop" else {"mining"})
    return accepted, verify if action in ("mining_stop", "mining_start") else None


def whatsminer(transport, record, action, credentials):
    if credentials is None:
        raise AuthenticationError("Введите учётные данные Whatsminer")
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
        salt = info.get("msg", {}).get("salt")
        if not salt:
            raise AuthenticationError("Whatsminer не выдал salt")
        timestamp = int(time.time())
        source = f"{command}{credentials.password}{salt}{timestamp}".encode()
        token = base64.b64encode(hashlib.sha256(source).digest()).decode()[:8]
        result = transport.rpc_packet(sock, {"cmd": command, "param": parameter, "ts": timestamp, "token": token, "account": credentials.username})
    def verify():
        info = transport.rpc("get.device.info")
        working = info.get("msg", {}).get("miner", {}).get("working")
        return str(working).lower() == ("false" if action == "mining_stop" else "true")
    return result.get("code") == 0, verify if action in ("mining_stop", "mining_start") else None


def elphapex(transport, record, action, credentials):
    definitions = {
        "identify_on": ("ftm_ledtest.cgi", {"leds_blue": 1, "leds_red": 0, "leds_flash": 1, "leds_time": 0}),
        "identify_off": ("ftm_ledtest.cgi", {"leds_blue": 0, "leds_red": 0, "leds_flash": 0, "leds_time": 0}),
        "reboot": ("reboot.cgi", {}), "mining_stop": ("setworkmode.cgi", {"workmode": "-1000"}),
        "mining_start": ("setworkmode.cgi", {"workmode": "0"}),
    }
    path, payload = definitions[action]
    accepted = _http_accept(transport, "/cgi-bin/luci/" + path, payload=payload)
    def verify():
        data = transport.http_json("/cgi-bin/luci/get_miner_conf.cgi") or {}
        return str(data.get("fc-work-mode")) == ("-1000" if action == "mining_stop" else "0")
    return accepted, verify if action in ("mining_stop", "mining_start") else None


def jasminer(transport, record, action, credentials):
    if credentials is None:
        raise AuthenticationError("Введите учётные данные Jasminer")
    path = {"identify_on": "/cgi-bin/find_miner_on.cgi", "identify_off": "/cgi-bin/find_miner_off.cgi"}[action]
    return _http_accept(transport, path), None


def avalon(transport, record, action, credentials):
    command = {"identify_toggle": "ascset|0,led,0-1", "reboot": "ascset|0,reboot,1", "mining_stop": "ascset|0,softoff", "mining_start": "ascset|0,reboot,1"}[action]
    response = transport.text_command(command)
    return bool(re.search(r"(?:^|[,|])STATUS=[SI](?:[,|]|$)", response)), None


EXECUTORS = {"antminer": antminer, "pitbit": antminer, "vnish": vnish, "whatsminer": whatsminer, "elphapex": elphapex, "jasminer": jasminer, "avalon": avalon}
SUPPORTED_ACTIONS = {
    key: {"identify_on", "identify_off", "reboot", "mining_stop", "mining_start"}
    for key in ("antminer", "pitbit", "vnish", "whatsminer", "elphapex")
}
SUPPORTED_ACTIONS["jasminer"] = {"identify_on", "identify_off"}
SUPPORTED_ACTIONS["avalon"] = {"identify_toggle", "reboot", "mining_stop", "mining_start"}


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
        payload = {**config, **payload}
    accepted = _http_accept(transport, rule["path"], rule.get("method", "POST"), payload)
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
    op = Operation(ScanOptions(device_timeout=30), cancel)
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
            return finish("skipped", "Устройство или прошивка изменились; обновите выбор")
        profile = service.registry.by_id[record.identity.profile_id]
        rule = (profile.command_rules or {}).get(action)
        if rule is None and action not in SUPPORTED_ACTIONS.get(profile.control, set()):
            return finish("unsupported", "Для этого профиля действие не реализовано")
        if action not in profile.verified_commands and not allow_unverified:
            return finish("unsupported", "Команда перенесена из старого кода, но не подтверждена для точной версии прошивки")
        op.remaining()
        # Persist uncertain intent before any write. A crash must never cause replay.
        finish("unconfirmed", "Выполнение начато; конечный результат пока неизвестен")
        with service.transport_factory(ip, op, service.credentials_for(ip)) as transport:
            if rule is not None:
                accepted, verify = declarative_command(transport, rule)
            else:
                accepted, verify = EXECUTORS[profile.control](transport, record, action, service.credentials_for(ip))
            if not accepted:
                return finish("failed", "API отклонил команду")
            if verify is None:
                return finish("unconfirmed", "API принял команду; состояние не подтверждено", True)
            for _ in range(3):
                op.pause(1)
                try:
                    if verify():
                        return finish("succeeded", "Ожидаемое состояние подтверждено чтением API", True)
                except (OSError, requests.RequestException, ProtocolError):
                    continue
            return finish("unconfirmed", "API принял команду, но ожидаемое состояние не подтверждено", True)
    except Cancelled:
        return finish("unconfirmed" if service.repository.command_result(command_id) else "cancelled", "Операция остановлена; отправленная команда не повторяется")
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
