"""Reviewed, local pool configuration contracts; no GUI or device guessing.

Stock uses the same audited read-to-write maps as the web configuration form.
VNish requires the current local OpenAPI settings declaration, not just its
firmware name. Read-only telemetry and compatibility with other commands do not
authorize a pool write. Raw settings, authentication and passwords stay ephemeral.
"""
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json

import requests

from . import stock_compatibility
from .runtime import AuthenticationError
from .vnish_compatibility import local_api_spec
from .pool_evidence import configuration_evidence, api_evidence


class PoolBackendError(Exception):
    """Fixed public explanation; never attach a raw response or credentials."""

    def __init__(self, message, status="unsupported"):
        super().__init__(message)
        self.status = status


@dataclass
class BackendState:
    backend_id: str
    slots: list[dict[str, str]] = field(repr=False)
    opaque: dict = field(default_factory=dict, repr=False)
    preserves_unknown_passwords: bool = False


def unsupported_reason(record):
    """Return a candidate routing decision, not an assertion of write support."""
    identity = record.identity
    firmware = str(identity.firmware).casefold()
    make = str(identity.make).casefold()
    profile = str(identity.profile_id)
    if (make == "bitmain" and firmware == "stock"
            and (profile == "bitmain.stock" or profile.startswith("bitmain.stock."))):
        return None
    if (make == "bitmain" and firmware == "vnish"
            and (profile == "bitmain.vnish" or profile.startswith("bitmain.vnish."))):
        return None
    if make == "microbt" and firmware == "stock" and profile == "microbt.rpc3":
        return None
    if make == "elphapex" and firmware == "stock" and profile == "elphapex.luci":
        from .elphapex_pool_backend import eligible
        if eligible(record):
            return None
    if firmware == "pitbit":
        from .pitbit_pool_backend import eligible
        if eligible(record):
            return None
        return "PitBit: чтение пулов доступно, но контракт их записи не подтверждён"
    if make in {"microbt", "whatsminer"} or "whatsminer" in str(identity.profile_id):
        return ("WhatsMiner: команда настройки пулов документирована, но сохранение "
                "неизвестных паролей и чтение сохранённой конфигурации не подтверждены")
    if make == "elphapex":
        return "Elphapex: контракт записи пулов и сохранения нетронутых настроек не подтверждён"
    if make in {"canaan", "avalon"}:
        return "Avalon: чтение CGMiner не подтверждает запись и сохранение настроек пулов"
    if make == "jasminer":
        return "Jasminer: контракт записи настроенных слотов пулов не подтверждён"
    if make == "ipollo":
        return "iPollo: контракт записи настроенных слотов пулов не подтверждён"
    if make == "goldshell":
        return "Goldshell: чтение MCB не подтверждает контракт записи пулов"
    return "Для этого интерфейса не подтверждён контракт записи настроек пулов"


def _identity_key(record):
    identity = record.identity
    return tuple(getattr(identity, key, None) for key in (
        "device_id", "profile_id", "profile_version", "fingerprint", "make",
        "model", "firmware", "firmware_version", "api_version"))


def _serial(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _configuration(state, ignore_pools=False):
    config = deepcopy(state.opaque.get("config", {}))
    if ignore_pools:
        if state.backend_id in {"bitmain.stock.cgi", "whatsminer.rpc3.pools", "elphapex.web.pools",
                                "pitbit.c096.web.pools"}:
            config.pop("pools", None)
        elif state.backend_id == "vnish.settings":
            miner = config.get("miner")
            if isinstance(miner, dict):
                miner.pop("pools", None)
    return {"backend": state.backend_id, "identity": state.opaque.get("identity"),
            "contract": state.opaque.get("contract"), "config": config}


def state_signature(state):
    """Digest the actual read configuration, excluding auth/session artifacts."""
    return hashlib.sha256(_serial(_configuration(state)).encode("utf-8")).hexdigest()


def equivalent_state(first, second, *, ignore_pools=False):
    """Compare typed settings, including unrelated fields on whole-config APIs."""
    return _serial(_configuration(first, ignore_pools)) == _serial(_configuration(second, ignore_pools))


def _slots(value, *, require_all=False):
    if not isinstance(value, list) or not 0 <= len(value) <= 3:
        raise PoolBackendError("Конфигурация не содержит изученный список слотов 1–3")
    for slot in value:
        if (not isinstance(slot, dict) or set(slot) - {"url", "user", "pass"}
                or any(not isinstance(item, str) for item in slot.values())
                or (require_all and set(slot) != {"url", "user", "pass"})):
            raise PoolBackendError("Формат полей настроенного пула не соответствует изученному контракту")
    return deepcopy(value)


def _credentials(service, record):
    credentials = service.credentials_for(record.identity.ip)
    if credentials is None:
        raise PoolBackendError("Настройте доступ к ASIC перед изменением пулов", "auth_required")
    return credentials


def _stock_contract(record, config, evidence):
    """Pool forms can exist even when a UI exposes no power-mode buttons."""
    model = stock_compatibility.model_name(record.identity.model)
    catalog = stock_compatibility.interfaces()["miner"]
    known = catalog.get(evidence.get("miner"))
    if known is not None:
        if (known.get("mode_update_only") or model not in known["modes"] or
                ("pages" in known and evidence.get("miner_page") not in known["pages"])):
            raise PoolBackendError("Текущая страница настройки пулов не соответствует изученному интерфейсу")
        mapping = stock_compatibility.config_mapping(config, known)
        candidates = [(known, mapping)] if mapping else []
    else:
        candidates = []
        for definition in catalog.values():
            if (definition.get("mode_update_only") or model not in definition["modes"]
                    or not definition.get("config_fallback", True)):
                continue
            mapping = stock_compatibility.config_mapping(config, definition)
            if mapping:
                candidates.append((definition, mapping))
    if not candidates:
        raise PoolBackendError("Не подтверждено сохранение всех полей текущей конфигурации Bitmain")
    selected = candidates[0]
    signatures = {(definition["write_mode"], definition.get("preserve_mode_type") is True,
                   _serial(mapping)) for definition, mapping in candidates}
    if len(signatures) != 1:
        raise PoolBackendError("Несколько известных интерфейсов требуют разных запросов записи пулов")
    definition, mapping = selected
    if mapping.get("pools") != "pools":
        raise PoolBackendError("Не подтверждено соответствие поля пулов при записи")
    contract = {"mapping": deepcopy(mapping), "mode_field": definition["write_mode"]}
    if definition.get("preserve_mode_type") is True:
        contract["preserve_mode_type"] = True
    return contract


def _read_stock(service, record, transport):
    _credentials(service, record)
    config = transport.http_json("/cgi-bin/get_miner_conf.cgi")
    safe = configuration_evidence(config, 'stock.cgi', record=record)
    try:
        evidence = stock_compatibility.probe(transport)
        contract = _stock_contract(record, config, evidence)
        slots = _slots(config["pools"])
    except Exception as exc:
        exc.pool_evidence = safe
        raise
    flag = config.get("bitmain-user-ip-cat")
    return BackendState("bitmain.stock.cgi", slots, {
        "config": deepcopy(config), "contract": contract, "identity": _identity_key(record),
        "safe_evidence": safe,
        # An unfamiliar non-false flag must not promise deterministic workers.
        "worker_auto_suffix": flag is not None and not (
            flag is False or (type(flag) in (str, int) and str(flag).lower() in {"0", "false"})),
    })


def _schema(spec, value, visited=()):
    """Only bounded, local JSON pointers; never fetch a spec-provided URL."""
    if not isinstance(value, dict) or len(visited) > 12:
        return None
    if "$ref" in value:
        ref = value["$ref"]
        prefix = "#/components/schemas/"
        if (not isinstance(ref, str) or not ref.startswith(prefix) or ref in visited
                or "/" in ref[len(prefix):] or "~" in ref[len(prefix):]):
            return None
        target = spec.get("components", {}).get("schemas", {}).get(ref[len(prefix):])
        return _schema(spec, target, (*visited, ref))
    if "oneOf" in value:
        alternatives = value["oneOf"]
        if not isinstance(alternatives, list) or not 1 <= len(alternatives) <= 3:
            return None
        objects = [item for item in alternatives if item != {"type": "null"}]
        return _schema(spec, objects[0], (*visited, "oneOf")) if len(objects) == 1 else None
    return value


def _object_with(spec, value, allowed_required):
    value = _schema(spec, value)
    if (not isinstance(value, dict) or value.get("type") != "object"
            or not isinstance(value.get("properties"), dict)
            or not isinstance(value.get("required", []), list)
            or not set(value.get("required", [])) <= set(allowed_required)):
        return None
    return value


def _vnish_pool_array(spec, value):
    value = _schema(spec, value)
    if (not isinstance(value, dict) or value.get("type") not in ("array", ["array", "null"])
            or value.get("items") is not False
            or not isinstance(value.get("prefixItems"), list) or len(value["prefixItems"]) != 3):
        return False
    for candidate in value["prefixItems"]:
        pool = _object_with(spec, candidate, {"url", "user", "pass"})
        if (pool is None or set(pool.get("required", [])) != {"url", "user", "pass"}
                or set(pool["properties"]) != {"url", "user", "pass"}
                or any(_schema(spec, prop) != {"type": "string"} for prop in pool["properties"].values())):
            return False
    return True


def _vnish_contract(spec):
    if not isinstance(spec, dict) or not local_api_spec(spec):
        raise PoolBackendError("VNish не объявил изученный локальный API настроек")
    try:
        paths = spec["paths"]["/settings"]
        get, post = paths["get"], paths["post"]
        root = _object_with(spec, post["requestBody"]["content"]["application/json"]["schema"], {"miner"})
        if root is None:
            raise ValueError
        miner = _object_with(spec, root["properties"]["miner"], {"pools"})
        if miner is None or not _vnish_pool_array(spec, miner["properties"]["pools"]):
            raise ValueError
        view = _object_with(spec, get["responses"]["200"]["content"]["application/json"]["schema"],
                            {"miner", "ui", "regional", "network", "layout", "password"})
        if view is None:
            raise ValueError
        view_miner = _object_with(spec, view["properties"]["miner"], {"pools"})
        if view_miner is None or not _vnish_pool_array(spec, view_miner["properties"]["pools"]):
            raise ValueError
        saved = _object_with(spec, post["responses"]["200"]["content"]["application/json"]["schema"],
                             {"restart_required", "reboot_required"})
        if (saved is None or set(saved.get("required", [])) != {"restart_required", "reboot_required"}
                or any(not isinstance(saved["properties"].get(key), dict)
                       or saved["properties"][key].get("type") != "boolean"
                       or set(saved["properties"][key]) - {"type", "description"}
                       for key in ("restart_required", "reboot_required"))):
            raise ValueError
    except (KeyError, TypeError, AttributeError, ValueError):
        raise PoolBackendError("VNish: схема чтения или записи пулов изменилась; запрос записи запрещён") from None
    # The payload is deliberately limited to this reviewed nested field.
    return {"path": "/api/v1/settings", "payload": "miner.pools"}


def _vnish_headers(service, record, transport):
    credentials = _credentials(service, record)
    candidates = service.credential_candidates(record.identity.ip, "vnish")
    candidates = list(dict.fromkeys(candidates or [credentials]))[:3]
    for candidate in candidates:
        try:
            response = transport.http_json("/api/v1/unlock", "POST", payload={"pw": candidate.password})
        except AuthenticationError:
            continue
        token = response.get("token") if isinstance(response, dict) else None
        if not isinstance(token, str) or not token:
            break
        service.remember_credentials(record.identity.ip, candidate)
        return {"Authorization": "Bearer " + token, "Cache-Control": "no-cache"}
    raise PoolBackendError("VNish не выдал токен для доступа к настройкам", "auth_required")


def _read_vnish(service, record, transport):
    spec = transport.http_json("/docs/api-doc.json")
    safe = {'read_source': 'vnish.settings', 'api': api_evidence(spec)}
    try:
        contract = _vnish_contract(spec)
        headers = _vnish_headers(service, record, transport)
        config = transport.http_json("/api/v1/settings", headers=headers)
        safe.update(configuration_evidence(config, 'vnish.settings', record=record))
        if not isinstance(config, dict) or not isinstance(config.get("miner"), dict):
            raise PoolBackendError("VNish не вернул сохранённую конфигурацию пулов")
        # Privacy-filtered fields remain missing, never guessed or defaulted.
        slots = _slots(config["miner"].get("pools"))
        if any(not {"url", "user"} <= slot.keys() for slot in slots):
            raise PoolBackendError("VNish не вернул адрес и worker каждого настроенного слота")
    except Exception as exc:
        exc.pool_evidence = safe
        raise
    return BackendState("vnish.settings", slots, {
        "config": deepcopy(config), "contract": contract, "identity": _identity_key(record),
        "headers": headers, "restart_required": False, "reboot_required": False,
        "safe_evidence": safe,
    })


def read_state(service, record, transport):
    """Read configuration and recognize a reviewed write contract, without writes.

    VNish's unlock exchanges a credential for a session token; it does not modify
    mining settings. Unsupported firmware families are rejected before any I/O.
    """
    reason = unsupported_reason(record)
    if reason:
        raise PoolBackendError(reason)
    try:
        if str(record.identity.firmware).casefold() == "pitbit":
            from .pitbit_pool_backend import read_state as read_pitbit
            return read_pitbit(service, record, transport)
        if record.identity.profile_id == "elphapex.luci":
            from .elphapex_pool_backend import read_state as read_elphapex
            return read_elphapex(service, record, transport)
        if record.identity.profile_id == "microbt.rpc3":
            from .whatsminer_pool_backend import read_state as read_whatsminer
            return read_whatsminer(service, record, transport)
        if str(record.identity.firmware).casefold() == "vnish":
            return _read_vnish(service, record, transport)
        return _read_stock(service, record, transport)
    except AuthenticationError:
        raise PoolBackendError("Нет доступа к настройкам ASIC; проверьте профиль доступа", "auth_required") from None


def _masked(value):
    if isinstance(value, dict):
        return any(_masked(item) for item in value.values())
    if isinstance(value, list):
        return any(_masked(item) for item in value)
    return isinstance(value, str) and bool(value) and set(value) <= {"*", "•"}


def _password_slots(value):
    """Canonical private intent, never inferred from configuration contents."""
    if (not isinstance(value, tuple) or any(type(slot) is not int or not 0 <= slot < 3 for slot in value)
            or value != tuple(sorted(set(value)))):
        raise PoolBackendError("Подготовленные данные изменились; обновите предпросмотр", "changed")
    return value


def masked_pool_fields(slots, *, explicit_password_slots=()):
    """Only explicit password input can disambiguate a mask-like write value.

    Readback callers use the empty default: a mask-like API password is never
    evidence of the entered secret, even if its characters happen to match.
    """
    explicit = _password_slots(explicit_password_slots)
    return any(_masked(value) for position, slot in enumerate(slots)
               for key, value in slot.items() if not (key == "pass" and position in explicit))


def _write_payload(before, slots, *, explicit_password_slots=()):
    """Construct only a reviewed payload; shared by preview and dispatch."""
    explicit_password_slots = _password_slots(explicit_password_slots)
    if before.backend_id == "pitbit.c096.web.pools":
        from .pitbit_pool_backend import payload
        return payload(before, slots, explicit_password_slots=explicit_password_slots)
    if before.backend_id == "elphapex.web.pools":
        from .elphapex_pool_backend import payload
        return payload(before, slots, explicit_password_slots=explicit_password_slots)
    if before.backend_id == "whatsminer.rpc3.pools":
        from .whatsminer_pool_backend import payload
        return payload(slots)
    slots = _slots(slots, require_all=before.backend_id == "vnish.settings")
    if before.backend_id == "bitmain.stock.cgi":
        config = before.opaque.get("config")
        if (not isinstance(config, dict) or not slots
                or _masked({key: value for key, value in config.items() if key != "pools"})
                or masked_pool_fields(slots, explicit_password_slots=explicit_password_slots)):
            raise PoolBackendError("Полная запись конфигурации не может сохранить скрытые поля")
        # An empty disabled slot can remain an omitted object exactly as the
        # reviewed Stock form does. An enabled slot needs every secret/value.
        if any((slot.get("url") or slot.get("user"))
               and not {"url", "user", "pass"} <= slot.keys() for slot in slots):
            raise PoolBackendError("Не получены все поля настроенных пулов; задайте недостающие значения явно")
        try:
            contract = before.opaque["contract"]
            mapping = contract["mapping"]
            mode_field = contract["mode_field"]
            if (mode_field not in {"miner-mode", "bitmain-work-mode"} or not isinstance(mapping, dict)
                    or mapping.get("pools") != "pools"):
                raise ValueError
            payload = {target: deepcopy(config[source]) for target, source in mapping.items()}
            # Most reviewed forms submit a numeric mode. The audited S19k Pro
            # form copies the saved value without converting its JSON type.
            mode = config["bitmain-work-mode"]
            payload[mode_field] = (deepcopy(mode) if contract.get("preserve_mode_type") is True
                                   else int(mode))
        except (KeyError, TypeError, ValueError, AttributeError):
            raise PoolBackendError("Подготовленная схема конфигурации Bitmain не соответствует контракту записи") from None
        payload["pools"] = slots
        return payload
    if before.backend_id == "vnish.settings":
        if masked_pool_fields(slots, explicit_password_slots=explicit_password_slots):
            raise PoolBackendError("Скрытые пароли пулов нельзя пересылать как новые значения")
        return {"miner": {"pools": slots}}
    raise PoolBackendError("Неизвестный подготовленный контракт записи пулов")


def validate_state(state, slots, *, explicit_password_slots=()):
    """Validate the exact write without I/O, for a truthful ready preview."""
    explicit_password_slots = _password_slots(explicit_password_slots)
    if state.backend_id == "whatsminer.rpc3.pools":
        from .whatsminer_pool_backend import validate_state as validate_whatsminer
        validate_whatsminer(state, slots)
        return
    _write_payload(state, slots, explicit_password_slots=explicit_password_slots)


def _accept(status, raw):
    if 500 <= status <= 599:
        return None
    if status != 200:
        return False
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError):
        return None
    if not isinstance(result, dict):
        return None
    if result.get("success") is False or result.get("error"):
        return False
    if "stats" in result and result["stats"] != "success":
        return False
    if "code" in result:
        code = result["code"]
        if (type(code) is int and code == 0) or (type(code) is str and code in {"0", "B000", "M000"}):
            return True
        # A numeric float or a boolean is not the reviewed integer/string ABI.
        return False if type(code) in (int, str) else None
    # HTTP success alone (including an empty JSON object) proves no API ack.
    # Stock's reviewed successful replies include a recognized command code.
    return None


def write_state(service, record, transport, before, slots, *, explicit_password_slots=()):
    """Write once using a prepared state; never reboot or repeat on uncertainty."""
    explicit_password_slots = _password_slots(explicit_password_slots)
    reason = unsupported_reason(record)
    expected_backend = ("pitbit.c096.web.pools" if str(record.identity.firmware).casefold() == "pitbit" else
                        "elphapex.web.pools" if record.identity.profile_id == "elphapex.luci" else
                        "whatsminer.rpc3.pools" if record.identity.profile_id == "microbt.rpc3" else
                        "vnish.settings" if str(record.identity.firmware).casefold() == "vnish" else "bitmain.stock.cgi")
    if reason or before.backend_id != expected_backend:
        raise PoolBackendError(reason or "Подготовленный контракт не соответствует семейству ASIC")
    if before.opaque.get("identity") != _identity_key(record):
        raise PoolBackendError("Идентичность ASIC изменилась после чтения настроек", "changed")
    if before.backend_id == "pitbit.c096.web.pools":
        from .pitbit_pool_backend import write_state as write_pitbit
        return write_pitbit(service, record, transport, before, slots,
                            explicit_password_slots=explicit_password_slots)
    if before.backend_id == "elphapex.web.pools":
        from .elphapex_pool_backend import write_state as write_elphapex
        return write_elphapex(service, record, transport, before, slots,
                              explicit_password_slots=explicit_password_slots)
    if before.backend_id == "whatsminer.rpc3.pools":
        from .whatsminer_pool_backend import write_state as write_whatsminer
        return write_whatsminer(service, record, transport, before, slots)
    _credentials(service, record)
    payload = _write_payload(before, slots, explicit_password_slots=explicit_password_slots)
    if before.backend_id == "bitmain.stock.cgi":
        try:
            status, raw = transport.http("/cgi-bin/set_miner_conf.cgi", "POST", payload=payload)
        except (OSError, requests.RequestException):
            return None
        return _accept(status, raw)
    if before.backend_id == "vnish.settings":
        try:
            status, raw = transport.http("/api/v1/settings", "POST", payload=payload,
                                         headers=before.opaque["headers"])
        except (OSError, requests.RequestException):
            return None
        if status != 200:
            return None if status >= 500 else False
        try:
            response = json.loads(raw)
        except (ValueError, UnicodeError):
            return None
        if not isinstance(response, dict):
            return None
        if response.get("error") or response.get("success") is False:
            return False
        if any(type(response.get(key)) is not bool for key in ("restart_required", "reboot_required")):
            return None
        for key in ("restart_required", "reboot_required"):
            before.opaque[key] = response[key]
        return True
    raise PoolBackendError("Неизвестный подготовленный контракт записи пулов")
