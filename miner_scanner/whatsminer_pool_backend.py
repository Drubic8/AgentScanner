"""WhatsMiner RPC3 pool writes using the verified vendor PKCS7 reference.

The public documentation's zero-padded example is inconsistent with its Python
demo and maintained vendor client. This module deliberately implements the
reference AES-256-ECB/PKCS7 once, never a padding fallback or retry. Runtime reads
cannot confirm persisted settings or a password: every sent password must have
been provided explicitly by the caller.
"""
import base64
from copy import deepcopy
import hashlib
import json
import re
import time

import requests

from .pool_backends import BackendState, PoolBackendError, _identity_key
from .pool_evidence import configuration_evidence, _secrets
from .profiles import first_field
from .runtime import ProtocolError
from .whatsminer_compatibility import info_message


BACKEND_ID = "whatsminer.rpc3.pools"
COMMAND = "set.miner.pools"
CONTRACT = {"protocol": "whatsminer.rpc3", "command": COMMAND, "pool_ids": [1, 2, 3],
            "cipher": "aes256-ecb-pkcs7", "verification": "runtime_without_passwords"}
_ACCOUNTS = frozenset({"super", "user1", "user2", "user3"})


def eligible(record):
    identity = record.identity
    return (str(identity.make).casefold() == "microbt" and str(identity.firmware).casefold() == "stock"
            and identity.profile_id == "microbt.rpc3")


def _credential_problem(credentials):
    if credentials is None:
        return "WhatsMiner RPC 3: настройте учётную запись API и её пароль"
    if not isinstance(credentials.username, str) or credentials.username not in _ACCOUNTS:
        return "WhatsMiner RPC 3: укажите настроенную учётную запись super или user1–user3"
    if not isinstance(credentials.password, str) or not credentials.password:
        return "WhatsMiner RPC 3: укажите непустой пароль учётной записи API"
    return None


def _crypto():
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError:
        raise PoolBackendError("Для изменения пулов WhatsMiner требуется установленная библиотека cryptography") from None
    return Cipher, algorithms, modes


def encrypt_parameter(key, plaintext):
    """Pure reference encoding, with exactly one PKCS7 padding operation."""
    Cipher, algorithms, modes = _crypto()
    if not isinstance(key, bytes) or len(key) != 32 or not isinstance(plaintext, bytes):
        raise PoolBackendError("Не подтверждён формат параметров шифрования WhatsMiner")
    pad = 16 - len(plaintext) % 16
    padded = plaintext + bytes([pad]) * pad
    cipher = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return base64.b64encode(cipher.update(padded) + cipher.finalize()).decode("ascii")


def payload(slots):
    """Logical parameter array; no omission, masking or default passwords."""
    if not isinstance(slots, list) or len(slots) != 3:
        raise PoolBackendError("WhatsMiner требует три однозначно определённых слота пула")
    result = []
    for slot in slots:
        if (not isinstance(slot, dict) or set(slot) != {"url", "user", "pass"}
                or any(not isinstance(slot[key], str) for key in ("url", "user", "pass"))):
            raise PoolBackendError("WhatsMiner: задайте пароль явно для всех трёх отправляемых пулов")
        # Empty, spaces, punctuation, Unicode and all-star strings are explicit
        # values, not placeholders. They must reach the API without trimming.
        result.append({"pool": slot["url"], "worker": slot["user"], "passwd": slot["pass"]})
    return result


def validate_state(state, slots):
    if state.backend_id != BACKEND_ID:
        raise PoolBackendError("Подготовленный контракт не соответствует WhatsMiner RPC 3")
    payload(slots)
    if state.opaque.get("config", {}).get("btrom-worker-suffix") != "":
        raise PoolBackendError("Для записи пулов WhatsMiner требуется подтверждённый пустой суффикс worker")
    problem = state.opaque.get("write_auth_problem")
    if problem:
        raise PoolBackendError(problem, "auth_required")
    _crypto()


def _success(response, command):
    if not isinstance(response, dict) or type(response.get("code")) is not int or response["code"] != 0 or response.get("desc") != command:
        denied = isinstance(response, dict) and type(response.get("code")) is int and response["code"] == -4
        raise PoolBackendError("WhatsMiner отказал в доступе к текущим настройкам" if denied else
                               "WhatsMiner не подтвердил формат ответа чтения текущих настроек",
                               "auth_required" if denied else "unsupported")
    return response


def _current(record, request):
    info = _success(request("get.device.info"), "get.device.info")
    msg = info_message(info)
    if msg is None:
        raise PoolBackendError("Не подтверждён документированный протокол WhatsMiner RPC 3.0")
    identity = record.identity
    system = msg["system"]
    firmware = system.get("fwversion")
    if (not isinstance(firmware, str) or not firmware or not isinstance(identity.firmware_version, str)
            or not identity.firmware_version or not re.fullmatch(r"3\.0\.\d+", str(identity.api_version))):
        raise PoolBackendError("Не подтверждены версия прошивки и API WhatsMiner перед настройкой пулов")
    if (msg["miner"]["type"] != identity.model or system["api"] != identity.api_version
            or firmware != identity.firmware_version):
        raise PoolBackendError("Устройство или версия WhatsMiner изменились перед настройкой пулов", "changed")
    for field, names in (("serial", {"serial", "serial_number", "sn"}),
                         ("mac", {"mac", "macaddr", "mac_address"})):
        known = getattr(identity, field, None)
        if isinstance(known, str) and known:
            current = first_field(info, names)
            if (not isinstance(current, str) or not current
                    or (current.casefold() != known.casefold() if field == "mac" else current != known)):
                raise PoolBackendError("Устройство или версия WhatsMiner изменились перед настройкой пулов", "changed")
    if type(system.get("apiswitch")) not in (str, int) or str(system["apiswitch"]) != "1":
        raise PoolBackendError("WhatsMiner: API Write выключен; запись пулов недоступна", "auth_required")
    salt = msg.get("salt")
    if not isinstance(salt, str) or not salt:
        raise PoolBackendError("WhatsMiner не выдал salt для авторизации настройки пулов", "auth_required")
    pools = _success(request("get.miner.status", "pools"), "get.miner.status")
    root = pools.get("msg")
    entries = root.get("pools") if isinstance(root, dict) else None
    if (not isinstance(entries, list) or len(entries) != 3
            or any(not isinstance(entry, dict) or type(entry.get("id")) is not int
                   or entry["id"] != index or not isinstance(entry.get("url"), str)
                   or not isinstance(entry.get("account"), str)
                   for index, entry in enumerate(entries, 1))):
        raise PoolBackendError("WhatsMiner не подтвердил упорядоченные уникальные ID пулов 1, 2, 3")
    # Runtime passwords are ignored even if a future response happens to expose
    # one. Only explicit caller-supplied values can enter a write parameter.
    slots = [{"url": entry["url"], "user": entry["account"]} for entry in entries]
    setting = _success(request("get.miner.setting"), "get.miner.setting")
    settings = setting.get("msg")
    suffix = settings.get("btrom-worker-suffix") if isinstance(settings, dict) else None
    if not isinstance(suffix, str):
        raise PoolBackendError("WhatsMiner не подтвердил настройку автоматического суффикса worker")
    return {"pools": slots, "btrom-worker-suffix": suffix}, salt, pools, info, setting


def read_state(service, record, transport):
    if not eligible(record):
        raise PoolBackendError("Текущий профиль не соответствует WhatsMiner RPC 3 Stock")
    config, _, pools, info, setting = _current(record, transport.rpc)
    credentials = service.credentials_for(record.identity.ip)
    secrets = _secrets(info) + _secrets(pools) + _secrets(setting)
    if credentials is not None:
        secrets += _secrets([credentials.username, credentials.password], sensitive=True)
    safe = configuration_evidence(pools, "whatsminer.rpc_pools", record=record, reading_kind="runtime", secrets=secrets)
    return BackendState(BACKEND_ID, deepcopy(config["pools"]), {
        "config": config, "contract": deepcopy(CONTRACT), "identity": _identity_key(record),
        "verification_kind": "runtime_without_passwords", "worker_auto_suffix": bool(config["btrom-worker-suffix"]),
        "write_auth_problem": _credential_problem(credentials), "safe_evidence": safe,
        "ephemeral_secrets": secrets,
    })


def _accepted(response):
    if not isinstance(response, dict):
        return None
    if response.get("error") or response.get("success") is False:
        return False
    if type(response.get("code")) is not int:
        return None
    if response["code"] != 0:
        return False
    return True if response.get("desc") == COMMAND else None


def write_state(service, record, transport, before, slots):
    if not eligible(record) or before.backend_id != BACKEND_ID or before.opaque.get("identity") != _identity_key(record):
        raise PoolBackendError("Идентичность WhatsMiner изменилась после предпросмотра", "changed")
    parameters = payload(slots)
    credentials = service.credentials_for(record.identity.ip)
    problem = _credential_problem(credentials)
    if problem:
        raise PoolBackendError(problem, "auth_required")
    # Check dependency availability before opening a socket or journaling a
    # setter. There is no Crypto/legacy-interface fallback.
    _crypto()
    with transport.connection(4433) as sock:
        def request(command, parameter=None):
            packet = {"cmd": command}
            if parameter is not None:
                packet["param"] = parameter
            return transport.rpc_packet(sock, packet)
        current, salt, _, _, _ = _current(record, request)
        if current["btrom-worker-suffix"]:
            raise PoolBackendError("Для записи пулов WhatsMiner требуется подтверждённый пустой суффикс worker")
        if current != before.opaque.get("config"):
            raise PoolBackendError("Пулы или суффикс WhatsMiner изменились после предпросмотра", "changed")
        timestamp = int(time.time())
        try:
            key = hashlib.sha256(f"{COMMAND}{credentials.password}{salt}{timestamp}".encode("utf-8")).digest()
            plaintext = json.dumps(parameters, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        except (UnicodeError, TypeError, ValueError):
            raise PoolBackendError("Не подтверждён формат отправляемых настроек WhatsMiner") from None
        packet = {"cmd": COMMAND, "ts": timestamp, "token": base64.b64encode(key).decode("ascii")[:8],
                  "account": credentials.username, "param": encrypt_parameter(key, plaintext)}
        try:
            response = transport.rpc_packet(sock, packet)
        except (OSError, requests.RequestException, ProtocolError):
            # A transmitted frame can have taken effect even if its reply was
            # lost/malformed. No second setter or alternative padding is sent.
            return None
    return _accepted(response)
