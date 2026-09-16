"""Query execution and parser adapters. A profile supplies the polling plan."""
import hashlib
import ipaddress
import json
import re
import time
from uuid import uuid4

import requests

from .models import DeviceIdentity, DeviceRecord
from .normalization import normalize, format_rate
from .profiles import first_field
from .runtime import AuthenticationError, ProtocolError
from .parsers.antminer import parse_antminer_stock
from .parsers.vnish import parse_antminer_vnish
from .parsers.pitbit import parse_antminer_pitbit
from .parsers.whatsminer import parse_whatsminer_data
from .parsers.elphapex import parse_elphapex
from .parsers.avalon import parse_avalon
from .parsers.ipollo import parse_ipollo
from .parsers.jasminer import parse_jasminer


def query(transport, definition):
    key, protocol, command, _ = definition[:4]
    options = definition[4] if len(definition) > 4 else {}
    try:
        if protocol == "cgminer":
            value = transport.cgminer(command, **options)
        elif protocol == "rpc":
            cmd, _, parameter = command.partition(":")
            value = transport.rpc(cmd, parameter or None, **options)
        elif protocol in {"html", "text"}:
            status, raw = transport.http(command, **options)
            value = raw.decode("utf-8", errors="replace") if status == 200 else None
        else:
            value = transport.http_json(command, "POST" if protocol == "http_post" else "GET", **options)
        return value
    except (OSError, requests.RequestException, AuthenticationError, ProtocolError, ValueError) as exc:
        # Never log response bodies, credentials or exception strings from external APIs.
        transport.operation.errors.append(f"{key}:{getattr(exc, 'code', type(exc).__name__)}")
        return None


def collect(profile, transport, *, seed=None, metadata=None, metadata_ttl=300):
    data = dict(seed or {})
    cached = dict(metadata or {})
    now = time.monotonic()
    for definition in profile.queries:
        key, _, _, is_metadata = definition[:4]
        transport.operation.remaining()
        if key in data:
            if is_metadata:
                cached[key] = (now, data[key])
            continue
        previous = cached.get(key)
        if is_metadata and previous and now - previous[0] < metadata_ttl:
            if previous[1] is not None:
                data[key] = previous[1]
            continue
        value = query(transport, definition)
        if is_metadata:
            cached[key] = (now, value)
        if value is not None:
            data[key] = value
    return data, cached


def parse_generic(ip, data):
    system = data.get("system", {})
    row = {"IP": ip, "Make": "Unknown", "Model": system.get("minertype", "ASIC (неизвестный профиль)"), "Algo": "Unknown"}
    if "web_status" in data:
        html = data["web_status"]
        for key, tag in (("Uptime", "bb_elapsed"), ("Real", "bb_ghs5s"), ("Avg", "bb_ghsav")):
            match = re.search(r'<cite id="' + tag + r'">([^<]*)</cite>', html)
            if match:
                row[key] = match.group(1)
        row["Temp"] = " ".join(re.findall(r'id="cbi-table-1-temp2"[^>]*>([^<]+)', html))
        row["Fan"] = " ".join(re.findall(r'id="bb_fan\d+"[^>]*>([^<]+)', html))
    return row


def warnings(data):
    parts = str(data.get("warnings", "")).strip().split(";")
    if len(parts) < 2 or not parts[0].strip() or "searchfailed" in parts[0].lower():
        return "", ""
    return f"ERR [{parts[0].strip()}]", "; ".join(parts[1:]).strip()


PARSERS = {
    "antminer": lambda ip, d: parse_antminer_stock(ip, d, diagnostics=warnings(d), work_mode=d.get("config", {}).get("bitmain-work-mode", d.get("config", {}).get("miner-mode"))),
    "vnish": lambda ip, d: parse_antminer_vnish(ip, d, {"info": d.get("vnish_info", {}), "summary": d.get("vnish_summary", {})}),
    "pitbit": lambda ip, d: parse_antminer_pitbit(ip, d, diagnostics=warnings(d), work_mode=None if "bitmain-work-mode" not in d.get("config", {}) else str(d["config"]["bitmain-work-mode"]) == "1"),
    "whatsminer": lambda ip, d: parse_whatsminer_data(ip, d.get("rpc_info", {}), d.get("rpc_summary", {}), d.get("rpc_pools", {})),
    "elphapex": lambda ip, d: parse_elphapex(ip, d.get("elphapex_stats", {}), d.get("elphapex_config", {})),
    "avalon": parse_avalon, "ipollo": parse_ipollo,
    "jasminer": lambda ip, d: parse_jasminer(ip, d.get("jasminer_status", {})),
    "generic": parse_generic, "cgminer_web": parse_generic,
}


def make_record(profile, ip, data, previous=None):
    try:
        row = PARSERS[profile.parser](ip, data) or parse_generic(ip, data)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        row = parse_generic(ip, data)
        row["Error"] = "PARTIAL DATA"
        row["ErrorDetails"] = "Ответ API не соответствует ожидаемой структуре"
    raw_model = first_field(data, {"model", "minertype", "product_type", "type", "g-model"})
    model = raw_model or row.get("Model", "Unknown")
    version = first_field(data, {"fw_version", "firmware_version", "system_filesystem_version", "miner_version"})
    api_version = first_field(data, {"api", "api_ver", "api_version"})
    serial = first_field(data, {"serial", "serial_number", "sn"})
    mac = first_field(data, {"mac", "macaddr", "mac_address"})
    fields = [profile.id, profile.version, model, version, api_version, serial, mac]
    fingerprint = hashlib.sha256(json.dumps(fields, ensure_ascii=True).encode()).hexdigest()
    same_hardware = previous and (serial or mac) and (serial, mac, model, profile.make) == (previous.identity.serial, previous.identity.mac, previous.identity.model, previous.identity.make)
    device_id = previous.identity.device_id if previous and (same_hardware or previous.identity.fingerprint == fingerprint) else str(uuid4())
    identity = DeviceIdentity(device_id, ip, profile.make, model, profile.firmware, version, api_version, serial, mac, profile.id, profile.version, fingerprint)
    snapshot = normalize(data, row, profile.parser, profile.metrics)
    row.setdefault("SortIP", int(ipaddress.IPv4Address(ip)))
    row.setdefault("Status", "Running" if snapshot.mining_state == "running" else "Unknown")
    row.setdefault("Error", "")
    row.setdefault("ErrorDetails", "")
    row.update({"Real": format_rate(snapshot.rate, snapshot.rate_unit), "Avg": format_rate(snapshot.average_rate, snapshot.rate_unit), "RawHash": snapshot.rate})
    if snapshot.uptime_seconds is not None:
        from .utils import get_uptime_str
        row["Uptime"] = get_uptime_str(snapshot.uptime_seconds)
    for key in ("Uptime", "Temp", "Fan", "Pool", "Worker"):
        row.setdefault(key, "—")
    actions = ("identify_on", "identify_off", "reboot", "mining_stop", "mining_start", "low", "normal_power", "hem", "identify_toggle")
    from .commands import SUPPORTED_ACTIONS
    implemented = SUPPORTED_ACTIONS.get(profile.control, set()) | set(profile.command_rules or {})
    capabilities = {action: "supported" if action in profile.verified_commands else "unverified" if action in implemented else "unsupported" for action in actions}
    return DeviceRecord(identity, snapshot, row, capabilities)
