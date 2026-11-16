"""Exact DG1+ web-form pool contract, independent of the vendor conf2 DTO.

The reviewed Vue form posts the complete saved configuration unchanged except
for edited pool fields. No aliases, underscore DTO, defaults, apply or reboot
are used here. Its HTTP success callback does not inspect an API ack, so only a
fresh saved configuration read can confirm the resulting settings.
"""
from copy import deepcopy
import hashlib
import re

import requests

from .pool_backends import (BackendState, PoolBackendError, _identity_key, _masked,
                            masked_pool_fields, state_signature)
from .pool_evidence import configuration_evidence
from .profiles import first_field
from .runtime import AuthenticationError, ProtocolError


BACKEND_ID = "elphapex.web.pools"
SOURCE = "elphapex.web.conf"
CONFIG_PATH = "/cgi-bin/get_miner_conf.cgi"
SYSTEM_PATH = "/cgi-bin/luci/get_system_info.cgi"
SETTER_PATH = "/cgi-bin/set_miner_conf.cgi"
FIRMWARE_VERSION = "DG1+_SW_V1.0.4"
STATIC_HASHES = {
    "/index.html": "fa14fcfb892e357a336637905b69063d5693187865acc99208acdb56e54f2e87",
    "/assets/index-575a0936.js": "81704ae716ee6354865fd65cddd3bfde63bc8053efc48d18697e31106fd54cb7",
    "/assets/Miner-f770af99.js": "e5bda05b68bd26ae4a354feb7cf8423644631383d0345ef971b941c5cb770416",
    "/assets/axios-82afda87.js": "f07c16d181da60ba16073976f341b39d9ead8fa0c1ce4f432af5f5e71732f9b2",
}
CONTRACT = {"read": CONFIG_PATH, "write": SETTER_PATH, "system": SYSTEM_PATH,
            "serialization": "whole-config-json", "model": "DG1+", "firmware": FIRMWARE_VERSION,
            "static_hashes": STATIC_HASHES}
_TYPES = {"pools": list, "fc-voltage": str, "fc-fan-ctrl": bool, "fc-freq-level": str,
          "fc-fan-pwm": str, "algo": str, "fc-work-mode": int, "fc-freq": str}


def _model(value):
    return re.sub(r"^Elphapex\s+", "", str(value), flags=re.IGNORECASE).strip().casefold()


def eligible(record):
    identity = record.identity
    return (str(identity.make).casefold() == "elphapex" and str(identity.firmware).casefold() == "stock"
            and identity.profile_id == "elphapex.luci" and _model(identity.model) == "dg1+"
            and identity.firmware_version == FIRMWARE_VERSION)


def _credentials(service, record):
    credentials = service.credentials_for(record.identity.ip)
    if (credentials is None or not isinstance(credentials.username, str)
            or not isinstance(credentials.password, str)):
        raise PoolBackendError("Elphapex: настройте профиль доступа к веб-интерфейсу", "auth_required")
    return credentials


def _slots(slots):
    if (not isinstance(slots, list) or len(slots) != 3
            or any(not isinstance(slot, dict) or set(slot) != {"url", "user", "pass"}
                   or any(type(value) is not str for value in slot.values()) for slot in slots)):
        raise PoolBackendError("Elphapex: не подтверждены три полных слота url/user/pass")
    return deepcopy(slots)


def _config(config):
    if (not isinstance(config, dict) or set(config) != set(_TYPES)
            or any(type(config[key]) is not expected for key, expected in _TYPES.items())):
        raise PoolBackendError("Elphapex: схема сохранённой конфигурации не соответствует изученному интерфейсу")
    _slots(config["pools"])
    return config


def _identity(record, system):
    if not isinstance(system, dict):
        raise PoolBackendError("Elphapex: не подтверждена текущая идентичность устройства", "changed")
    model = first_field(system, {"minertype"})
    version = first_field(system, {"system_filesystem_version"})
    if _model(model) != _model(record.identity.model) or version != record.identity.firmware_version:
        raise PoolBackendError("Elphapex: устройство или прошивка изменились перед настройкой пулов", "changed")
    mac = first_field(system, {"mac", "macaddr", "mac_address"})
    serial = first_field(system, {"serial", "serial_number", "sn"})
    known_mac, known_serial = record.identity.mac, record.identity.serial
    if known_mac and (mac is None or mac.casefold() != known_mac.casefold()):
        raise PoolBackendError("Elphapex: устройство или прошивка изменились перед настройкой пулов", "changed")
    # This verified system endpoint omits serial numbers. A matching known MAC
    # is sufficient when the scanner's fresh identity already includes a serial.
    if known_serial and ((serial is not None and serial != known_serial)
                         or (serial is None and not known_mac)):
        raise PoolBackendError("Elphapex: не подтверждена текущая идентичность устройства", "changed")


def _interface(transport):
    for path, expected in STATIC_HASHES.items():
        status, raw = transport.http(path)
        if status in {401, 403}:
            raise PoolBackendError("Elphapex: нет доступа к проверке веб-интерфейса", "auth_required")
        if status != 200 or not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != expected:
            raise PoolBackendError("Elphapex: текущий веб-интерфейс не соответствует изученному контракту")


def read_state(service, record, transport):
    if not eligible(record):
        raise PoolBackendError("Elphapex: запись изучена только для DG1+ Stock DG1+_SW_V1.0.4")
    credentials = _credentials(service, record)
    config = transport.http_json(CONFIG_PATH)
    secrets = [credentials.username, credentials.password]
    safe = configuration_evidence(config, SOURCE, record=record, reading_kind="saved_configuration", secrets=secrets)
    try:
        _config(config)
        _identity(record, transport.http_json(SYSTEM_PATH))
        _interface(transport)
    except AuthenticationError:
        error = PoolBackendError("Elphapex: нет доступа к проверке веб-интерфейса", "auth_required")
        error.pool_evidence = safe
        raise error from None
    except Exception as error:
        error.pool_evidence = safe
        raise
    return BackendState(BACKEND_ID, deepcopy(config["pools"]), {
        "config": deepcopy(config), "contract": deepcopy(CONTRACT), "identity": _identity_key(record),
        "safe_evidence": safe, "ephemeral_secrets": secrets,
    })


def payload(state, slots, *, explicit_password_slots=()):
    if state.backend_id != BACKEND_ID or state.opaque.get("contract") != CONTRACT:
        raise PoolBackendError("Подготовленный контракт не соответствует Elphapex DG1+")
    config = _config(state.opaque.get("config"))
    result = deepcopy(config)
    result["pools"] = _slots(slots)
    if (_masked({key: value for key, value in result.items() if key != "pools"})
            or masked_pool_fields(result["pools"], explicit_password_slots=explicit_password_slots)):
        raise PoolBackendError("Elphapex: полная запись не может сохранить скрытые поля")
    return result


def validate_state(state, slots, *, explicit_password_slots=()):
    payload(state, slots, explicit_password_slots=explicit_password_slots)


def write_state(service, record, transport, before, slots, *, explicit_password_slots=()):
    if not eligible(record) or before.opaque.get("identity") != _identity_key(record):
        raise PoolBackendError("Elphapex: идентичность изменилась после предпросмотра", "changed")
    outgoing = payload(before, slots, explicit_password_slots=explicit_password_slots)
    current = read_state(service, record, transport)
    if state_signature(current) != state_signature(before):
        raise PoolBackendError("Elphapex: конфигурация изменилась после предпросмотра", "changed")
    try:
        status, _ = transport.http(SETTER_PATH, "POST", payload=outgoing,
                                   headers={"Content-Type": "application/json"})
    except AuthenticationError:
        return False
    except (OSError, requests.RequestException, ProtocolError):
        return None
    # The reviewed Vue client does not inspect a response ack. No response body
    # or success rule from the unrelated conf2 endpoint is borrowed here.
    return False if type(status) is int and 400 <= status < 500 else None
