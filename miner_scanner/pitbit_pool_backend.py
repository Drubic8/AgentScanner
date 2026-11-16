"""Reviewed C096 React full-config pool form, not a Stock/C074 fallback.

Canonical user slots omit the wire-only indices. A write deep-copies all 23
saved fields and merges only url/user/pass into the existing indexed objects;
the separate hotel pools, fee and all controls remain untouched. No upgrade,
apply, reboot, alternate encoding or blind retry is performed.
"""
from copy import deepcopy
import hashlib
import json

import requests

from .pool_backends import (BackendState, PoolBackendError, _identity_key, _masked,
                            masked_pool_fields, state_signature)
from .pool_evidence import configuration_evidence
from .profiles import first_field
from .runtime import AuthenticationError, ProtocolError


BACKEND_ID = "pitbit.c096.web.pools"
SOURCE = "pitbit.web.conf"
CONFIG_PATH = "/cgi-bin/get_miner_conf.cgi"
SYSTEM_PATH = "/cgi-bin/get_system_info.cgi"
SETTER_PATH = "/cgi-bin/set_miner_conf.cgi"
ENGINE_TYPE, ENGINE_VERSION, ENGINE_API = "Antminer S21+_i", "incm.C096", "3.1"
HTTP_WRAPPER_VERSION = "Tue May 10 16:20:00 CST 2020"
STATIC_HASHES = {
    "/index.html": "11ddd18083fbd9d89422667e1df7f5bbffb04af65340c539ff75d71ba777347b",
    "/static/js/main.b3210ed9.js": "a29e3cd6ca15e21cb0ab63487e42035edae533912934fee2136f5fbd42685ec9",
}
CONTRACT = {"read": CONFIG_PATH, "write": SETTER_PATH, "serialization": "whole-config-json",
            "engine_type": ENGINE_TYPE, "engine_version": ENGINE_VERSION, "engine_api": ENGINE_API,
            "static_hashes": STATIC_HASHES, "user_pool_ids": [0, 1, 2], "upgrade_flag": False}
_TYPES = {
    "api-allow": str, "api-groups": str, "api-listen": bool, "api-network": bool,
    "bitmain-ccdelay": str, "bitmain-fan-ctrl": bool, "bitmain-fan-pwm": str,
    "bitmain-freq": str, "bitmain-freq-level": str, "bitmain-hashrate-percent": str,
    "bitmain-pwth": str, "bitmain-use-vil": bool, "bitmain-voltage": str,
    "bitmain-work-mode": int, "enable_working_voltage": bool, "freq_mode": str,
    "hotelfeepercent": str, "need_update_fw_now": bool, "pools": list,
    "poolshotelfee": list, "target-temp": str, "target-temp-ctrl": bool, "working_voltage": str,
}


def eligible(record):
    identity = record.identity
    return (str(identity.make).casefold() == "bitmain" and str(identity.firmware).casefold() == "pitbit"
            and identity.profile_id == "bitmain.pitbit" and identity.model == ENGINE_TYPE
            # Legacy make_record reads the HTTP build rather than VERSION.Miner.
            # This exact wrapper is only a candidate: _native must still prove
            # C096 before any prepared state or setter becomes available.
            and identity.firmware_version in {ENGINE_VERSION, HTTP_WRAPPER_VERSION}
            and identity.api_version == ENGINE_API)


def _credentials(service, record):
    credentials = service.credentials_for(record.identity.ip)
    if (credentials is None or not isinstance(credentials.username, str)
            or not isinstance(credentials.password, str)):
        raise PoolBackendError("PitBit C096: настройте профиль доступа к веб-интерфейсу", "auth_required")
    return credentials


def _slots(slots):
    if (not isinstance(slots, list) or len(slots) != 3
            or any(not isinstance(slot, dict) or set(slot) != {"url", "user", "pass"}
                   or any(type(value) is not str for value in slot.values()) for slot in slots)):
        raise PoolBackendError("PitBit C096: не подтверждены три полных пользовательских слота url/user/pass")
    return deepcopy(slots)


def _config(config):
    if (not isinstance(config, dict) or set(config) != set(_TYPES)
            or any(type(config[key]) is not expected for key, expected in _TYPES.items())):
        raise PoolBackendError("PitBit C096: схема сохранённой конфигурации не соответствует изученному интерфейсу")
    if config["need_update_fw_now"] is not False:
        raise PoolBackendError("PitBit C096: запись пулов заблокирована при флаге обновления прошивки")
    pools = config["pools"]
    if (len(pools) != 3 or any(not isinstance(slot, dict)
            or set(slot) != {"index", "url", "user", "pass"}
            or type(slot.get("index")) is not int or slot["index"] != position
            or any(type(slot[field]) is not str for field in ("url", "user", "pass"))
            for position, slot in enumerate(pools))):
        raise PoolBackendError("PitBit C096: не подтверждены индексы пользовательских пулов 0, 1, 2")
    hotel = config["poolshotelfee"]
    if (len(hotel) != 1 or not isinstance(hotel[0], dict) or set(hotel[0]) != {"url", "user", "pass"}
            or any(type(value) is not str for value in hotel[0].values())):
        raise PoolBackendError("PitBit C096: не подтверждена полная конфигурация отдельного hotel pool")
    return config


def _native(transport):
    response = transport.cgminer("version")
    versions = response.get("VERSION") if isinstance(response, dict) else None
    if (not isinstance(versions, list) or len(versions) != 1 or not isinstance(versions[0], dict)
            or versions[0].get("Type") != ENGINE_TYPE or versions[0].get("Miner") != ENGINE_VERSION
            or versions[0].get("API") != ENGINE_API):
        raise PoolBackendError("PitBit C096: не подтверждены текущие native Type/Miner/API", "changed")
    statuses = response.get("STATUS")
    if (not isinstance(statuses, list) or not statuses
            or any(not isinstance(item, dict) or item.get("STATUS") != "S" for item in statuses)):
        raise PoolBackendError("PitBit C096: native API отказал в чтении версии")


def _hardware(record, system):
    if not isinstance(system, dict) or system.get("minertype") != "Antminer S21+":
        raise PoolBackendError("PitBit C096: не подтверждена текущая HTTP-оболочка устройства", "changed")
    # This wrapper's build is not the authoritative native engine version.
    mac = first_field(system, {"mac", "macaddr", "mac_address"})
    serial = first_field(system, {"serial", "serial_number", "sn"})
    known_mac, known_serial = record.identity.mac, record.identity.serial
    if known_mac and (mac is None or mac.casefold() != known_mac.casefold()):
        raise PoolBackendError("PitBit C096: идентичность устройства изменилась перед записью", "changed")
    if known_serial and ((serial is not None and serial != known_serial)
                         or (serial is None and not known_mac)):
        raise PoolBackendError("PitBit C096: идентичность устройства не подтверждена перед записью", "changed")


def _interface(transport):
    for path, digest in STATIC_HASHES.items():
        status, body = transport.http(path)
        if status in {401, 403}:
            raise PoolBackendError("PitBit C096: нет доступа к проверке веб-интерфейса", "auth_required")
        if status != 200 or not isinstance(body, bytes) or hashlib.sha256(body).hexdigest() != digest:
            raise PoolBackendError("PitBit C096: текущий веб-интерфейс не соответствует изученному контракту")


def read_state(service, record, transport):
    if not eligible(record):
        raise PoolBackendError("PitBit: запись изучена только для Antminer S21+_i incm.C096 API 3.1")
    credentials = _credentials(service, record)
    config = transport.http_json(CONFIG_PATH)
    secrets = [credentials.username, credentials.password]
    safe = configuration_evidence(config, SOURCE, record=record, reading_kind="saved_configuration", secrets=secrets)
    try:
        _config(config)
        _native(transport)
        _hardware(record, transport.http_json(SYSTEM_PATH))
        _interface(transport)
    except AuthenticationError:
        error = PoolBackendError("PitBit C096: нет доступа к проверке веб-интерфейса", "auth_required")
        error.pool_evidence = safe
        raise error from None
    except Exception as error:
        error.pool_evidence = safe
        raise
    slots = [{field: slot[field] for field in ("url", "user", "pass")} for slot in config["pools"]]
    return BackendState(BACKEND_ID, slots, {"config": deepcopy(config), "contract": deepcopy(CONTRACT),
        "identity": _identity_key(record), "safe_evidence": safe, "ephemeral_secrets": secrets})


def payload(state, slots, *, explicit_password_slots=()):
    if state.backend_id != BACKEND_ID or state.opaque.get("contract") != CONTRACT:
        raise PoolBackendError("Подготовленный контракт не соответствует PitBit C096")
    config = _config(state.opaque.get("config"))
    result = deepcopy(config)
    canonical = _slots(slots)
    for original, edited in zip(result["pools"], canonical):
        original.update(edited)
    # API ACL wildcards are valid configuration, not masked credentials.
    if (masked_pool_fields(canonical, explicit_password_slots=explicit_password_slots)
            or _masked(result["poolshotelfee"])):
        raise PoolBackendError("PitBit C096: скрытые поля пользовательских или hotel pools нельзя пересылать")
    return result


def validate_state(state, slots, *, explicit_password_slots=()):
    payload(state, slots, explicit_password_slots=explicit_password_slots)


def _accepted(status, raw):
    if type(status) is not int:
        return None
    if 400 <= status < 500:
        return False
    if not 200 <= status < 300:
        return None
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError, TypeError):
        return None
    if not isinstance(result, dict):
        return None
    if result.get("error") or result.get("success") is False or result.get("stats") == "error":
        return False
    return True if result.get("stats") == "success" else None


def write_state(service, record, transport, before, slots, *, explicit_password_slots=()):
    if not eligible(record) or before.opaque.get("identity") != _identity_key(record):
        raise PoolBackendError("PitBit C096: идентичность изменилась после предпросмотра", "changed")
    outgoing = payload(before, slots, explicit_password_slots=explicit_password_slots)
    current = read_state(service, record, transport)
    if state_signature(current) != state_signature(before):
        raise PoolBackendError("PitBit C096: конфигурация изменилась после предпросмотра", "changed")
    try:
        status, raw = transport.http(SETTER_PATH, "POST", payload=outgoing,
                                     headers={"Content-Type": "application/json"})
    except AuthenticationError:
        return False
    except (OSError, requests.RequestException, ProtocolError):
        return None
    return _accepted(status, raw)
