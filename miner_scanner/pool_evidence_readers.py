"""Explicit read-only evidence collection from reviewed local pool endpoints.

These reads do not establish write compatibility and never create a pool edit.
Saved configuration is distinguished from runtime telemetry. Endpoint paths and
commands are local constants; no URLs or commands supplied by an ASIC are used.
"""
import re

import requests

from .pool_backends import PoolBackendError, _vnish_headers
from .pool_evidence import configuration_evidence, api_evidence, identity_evidence, _secrets
from .runtime import AuthenticationError, ProtocolError
from .vnish_compatibility import local_api_spec
from .whatsminer_compatibility import info_message


def _route(service, record):
    identity = record.identity
    registry = getattr(service, "registry", None)
    by_id = getattr(registry, "by_id", {})
    profile = by_id.get(identity.profile_id) if isinstance(by_id, dict) else None
    if (profile is None or profile.make.casefold() != str(identity.make).casefold()
            or profile.firmware.casefold() != str(identity.firmware).casefold()):
        raise PoolBackendError("Для текущего профиля и прошивки не подтверждён безопасный источник сведений о пулах")
    if profile.version != identity.profile_version:
        raise PoolBackendError("Профиль устройства изменился; повторите сканирование", "changed")
    profile_id = profile.id
    parser = profile.parser
    if (profile.make == "Bitmain" and profile.firmware == "Stock" and parser == "antminer"
            and (profile_id == "bitmain.stock" or profile_id.startswith("bitmain.stock."))):
        return "stock"
    if (profile.make == "Bitmain" and profile.firmware == "PitBit" and parser == "pitbit"
            and (profile_id == "bitmain.pitbit" or profile_id.startswith("bitmain.pitbit."))):
        return "pitbit"
    if (profile.make == "Bitmain" and profile.firmware == "VNish" and parser == "vnish"
            and profile_id == "bitmain.vnish"):
        return "vnish"
    if (profile.make, profile.firmware, parser, profile_id) == ("MicroBT", "Stock", "whatsminer", "microbt.rpc3"):
        if not re.fullmatch(r"3\.0\.\d+", str(identity.api_version)):
            raise PoolBackendError("Не подтверждён протокол WhatsMiner RPC 3.0 для чтения сведений о пулах")
        return "whatsminer"
    routes = {
        ("Elphapex", "Stock", "elphapex", "elphapex.luci"): "elphapex",
        ("Canaan", "Stock", "avalon", "canaan.avalon"): "cgminer",
        ("iPollo", "Stock", "ipollo", "ipollo.cgminer"): "cgminer",
        ("Unknown", "Unknown", "generic", "generic.cgminer"): "cgminer",
        ("Jasminer", "Stock", "jasminer", "jasminer.web"): "jasminer",
        ("Goldshell", "Stock", "goldshell", "goldshell.mcb"): "goldshell",
    }
    route = routes.get((profile.make, profile.firmware, parser, profile_id))
    if route is None:
        raise PoolBackendError("Для этого интерфейса не подтверждён безопасный источник сведений о пулах")
    return route


def _describe(value, source, kind, record, *, shape="configuration", extra_secrets=()):
    """Unknown schemas remain useful evidence, never a successful pool read."""
    safe = configuration_evidence(value, source, record=record, reading_kind=kind)
    if extra_secrets:
        safe["identity"] = identity_evidence(record, secrets=_secrets(value) + list(extra_secrets))
    if shape == "array":
        usable = isinstance(value, list)
    elif not isinstance(value, dict):
        usable = False
    elif shape == "vnish":
        miner = value.get("miner")
        usable = isinstance(miner, dict) and isinstance(miner.get("pools"), list)
    elif shape == "rpc":
        usable = any(isinstance(value.get(key), dict) and isinstance(value[key].get("pools"), list)
                     for key in ("msg", "Msg"))
    elif shape == "cgminer":
        usable = isinstance(value.get("POOLS"), list)
    elif shape == "jasminer":
        pools = value.get("pools")
        usable = isinstance(pools, list) or (isinstance(pools, dict) and isinstance(pools.get("pool"), list))
    else:
        usable = isinstance(value.get("pools"), list)
    if isinstance(value, dict):
        if (value.get("error") or value.get("success") is False
                or ("stats" in value and value["stats"] != "success")):
            usable = False
        if "code" in value:
            code = value["code"]
            if not ((type(code) is int and code == 0)
                    or (type(code) is str and code in {"0", "M000", "B000"})):
                usable = False
        statuses = value.get("STATUS")
        if (shape == "cgminer" and isinstance(statuses, list)
                and any(isinstance(item, dict) and isinstance(item.get("STATUS"), str)
                        and item["STATUS"] in {"E", "F"} for item in statuses)):
            usable = False
    if not usable:
        error = PoolBackendError("Ответ не содержит подтверждённую структуру чтения пулов")
        error.pool_evidence = safe
        raise error
    return safe


def _read_whatsminer(record, transport):
    # A model label or open port alone never chooses a command protocol.
    response = transport.rpc("get.device.info")
    message = info_message(response)
    if message is None:
        raise PoolBackendError("WhatsMiner не подтвердил документированный интерфейс RPC 3.0")
    identity = record.identity
    if (message["miner"]["type"] != identity.model
            or message["system"]["api"] != identity.api_version
            or (identity.firmware_version and message["system"].get("fwversion") != identity.firmware_version)):
        raise PoolBackendError("Устройство или версия API изменились; повторите сканирование", "changed")
    pools = transport.rpc("get.miner.status", "pools")
    if (not isinstance(pools, dict) or type(pools.get("code")) is not int or pools.get("code") != 0
            or pools.get("desc") != "get.miner.status"):
        denied = isinstance(pools, dict) and type(pools.get("code")) is int and pools["code"] == -4
        error = PoolBackendError("WhatsMiner не разрешил чтение сведений о пулах" if denied else
                                 "WhatsMiner не подтвердил ответ команды чтения пулов",
                                 "auth_required" if denied else "unsupported")
        error.pool_evidence = configuration_evidence(pools, "whatsminer.rpc_pools", record=record, reading_kind="runtime")
        error.pool_evidence["identity"] = identity_evidence(record, secrets=_secrets(response) + _secrets(pools))
        raise error
    return _describe(pools, "whatsminer.rpc_pools", "runtime", record, shape="rpc", extra_secrets=_secrets(response))


def _read_vnish(service, record, transport):
    credentials = service.credentials_for(record.identity.ip)
    known = _secrets([credentials.username, credentials.password], sensitive=True) if credentials is not None else []
    spec = transport.http_json("/docs/api-doc.json")
    safe = {"read_source": "vnish.settings", "reading_kind": "saved_configuration",
            "api": api_evidence(spec), "identity": identity_evidence(record, secrets=known)}
    try:
        if not isinstance(spec, dict) or not local_api_spec(spec):
            raise PoolBackendError("VNish не объявил изученный локальный API чтения настроек")
        path = spec["paths"].get("/settings")
        operation = path.get("get") if isinstance(path, dict) else None
        if (not isinstance(operation, dict) or not isinstance(operation.get("responses"), dict)
                or not isinstance(operation["responses"].get("200"), dict)):
            raise PoolBackendError("VNish не объявил GET чтения сохранённых настроек")
        body = operation.get("requestBody", {})
        if not isinstance(body, dict) or body.get("required") is True or "$ref" in body:
            raise PoolBackendError("VNish требует неизвестное тело запроса чтения настроек")
        for owner in (path, operation):
            parameters = owner.get("parameters", [])
            if (not isinstance(parameters, list)
                    or any(not isinstance(parameter, dict) or parameter.get("required") is True
                           or "$ref" in parameter for parameter in parameters)):
                raise PoolBackendError("VNish требует неизвестные параметры чтения настроек")
            if "servers" in owner:
                servers = owner["servers"]
                if (not isinstance(servers, list) or not servers
                        or any(not isinstance(server, dict) or server.get("url") not in ("/api/v1", "/api/v1/")
                               for server in servers)):
                    raise PoolBackendError("VNish объявил неизвестный адрес API чтения настроек")
        # Known token exchange only; no settings POST or mining action. The
        # shared helper bounds credentials and honors the transport's auth rules.
        headers = _vnish_headers(service, record, transport)
        # The helper remembers the successfully authenticated candidate. Read
        # it from session memory, never credentials files, and mask both its
        # secrets and the actual returned token in successful/error metadata.
        authenticated = service.credentials_for(record.identity.ip)
        if authenticated is not None:
            known.extend(_secrets([authenticated.username, authenticated.password], sensitive=True))
        known.extend(_secrets(headers, sensitive=True))
        safe["identity"] = identity_evidence(record, secrets=known)
        config = transport.http_json("/api/v1/settings", headers=headers)
        result = _describe(config, "vnish.settings", "saved_configuration", record,
                           shape="vnish", extra_secrets=known)
        result["api"] = safe["api"]
        return result
    except PoolBackendError as exc:
        exc.pool_evidence = {**safe, **getattr(exc, "pool_evidence", {})}
        raise
    except AuthenticationError:
        error = PoolBackendError("Нет доступа VNish для чтения сохранённых настроек", "auth_required")
        error.pool_evidence = safe
        raise error from None


def read_evidence(service, record, transport):
    """Return detached field/type evidence; never return settings or send edits."""
    route = _route(service, record)
    try:
        if route == "stock":
            config = transport.http_json("/cgi-bin/get_miner_conf.cgi")
            source, kind = "stock.cgi", "saved_configuration"
        elif route == "pitbit":
            config = transport.http_json("/cgi-bin/get_miner_conf.cgi")
            source, kind = "antminer.cgi", "saved_configuration"
        elif route == "elphapex":
            config = transport.http_json("/cgi-bin/luci/get_miner_conf.cgi")
            source, kind = "elphapex.luci", "unknown"
        elif route == "whatsminer":
            return _read_whatsminer(record, transport)
        elif route == "cgminer":
            config = transport.cgminer("pools")
            source, kind = "cgminer.pools", "runtime"
        elif route == "jasminer":
            # This POST is the documented read endpoint, without a write body.
            config = transport.http_json("/cgi-bin/minerStatus.cgi", "POST")
            source, kind = "jasminer.status", "runtime"
        elif route == "goldshell":
            config = transport.http_json("/mcb/pools", expect_list=True)
            source, kind = "goldshell.pools", "unknown"
        else:
            return _read_vnish(service, record, transport)
        shape = {"cgminer": "cgminer", "jasminer": "jasminer", "goldshell": "array"}.get(route, "configuration")
        return _describe(config, source, kind, record, shape=shape)
    except AuthenticationError:
        raise PoolBackendError("Нет доступа для чтения сведений о пулах; проверьте профиль доступа", "auth_required") from None
    except ProtocolError:
        raise PoolBackendError("Не подтверждён формат ответа чтения сведений о пулах") from None
    except (OSError, requests.RequestException):
        raise PoolBackendError("Нет связи с устройством при чтении сведений о пулах", "offline") from None
