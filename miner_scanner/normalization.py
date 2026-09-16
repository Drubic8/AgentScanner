"""Numeric telemetry independent of display strings and magnitude heuristics."""
import math
import re
from .models import TelemetrySnapshot


UNITS = {"H/s": 1, "KH/s": 1e3, "MH/s": 1e6, "GH/s": 1e9, "TH/s": 1e12,
         "M/s": 1e6, "G/s": 1e9, "Sol/s": 1, "kSol/s": 1e3, "MSol/s": 1e6}


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (TypeError, ValueError):
        return None


def block(value, key):
    items = value.get(key, []) if isinstance(value, dict) else []
    result = {}
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                result.update(item)
    return result


def rate_from_fields(data, average=False):
    suffixes = ("av",) if average else ("5s", "1m", "5m")
    for prefix, multiplier in (("GHS", 1e9), ("MHS", 1e6), ("HS", 1)):
        for suffix in suffixes:
            value = number(data.get(f"{prefix} {suffix}"))
            if value is not None:
                return value * multiplier
    return None


def explicit_rate(value, unit=None):
    if isinstance(value, str) and unit is None:
        match = re.fullmatch(r"\s*([0-9.eE+\-]+)\s+([A-Za-z/]+)\s*", value)
        if match:
            value, unit = match.groups()
    numeric = number(value)
    if numeric is None or unit not in UNITS:
        return None, "H/s"
    return numeric * UNITS[unit], "Sol/s" if "Sol" in unit else "H/s"


def value_at(data, path):
    """A list of object keys/list indexes, without an expression language."""
    for part in path:
        try:
            data = data[part]
        except (KeyError, IndexError, TypeError):
            return None
    return data


def normalize(data, row, parser, metrics=None):
    snapshot = TelemetrySnapshot(algorithm=row.get("Algo", "Unknown"))
    values = {**block(data.get("stats"), "STATS"), **block(data.get("summary"), "SUMMARY")}
    snapshot.uptime_seconds = number(values.get("Elapsed"))
    for key, value in values.items():
        if re.fullmatch(r"fan\d+", key, re.I):
            numeric = number(value)
            if numeric is not None:
                snapshot.fan_rpm.append(numeric)
        if re.fullmatch(r"(?:temp2_|temp_chip|temp)\d+", key, re.I):
            components = value.split("-") if isinstance(value, str) and "-" in value else [value]
            snapshot.temperatures_c.extend(v for v in (number(x) for x in components) if v is not None)
    snapshot.rate = rate_from_fields(values)
    snapshot.average_rate = rate_from_fields(values, True)
    if parser == "avalon":
        if snapshot.rate is None:
            snapshot.rate = explicit_rate(values.get("GHSspd", values.get("GHSmm")), "GH/s")[0]
        if snapshot.average_rate is None:
            snapshot.average_rate = explicit_rate(values.get("GHSavg"), "GH/s")[0]
    elif parser == "ipollo":
        snapshot.rate, snapshot.rate_unit = explicit_rate(values.get("Hashrate"), values.get("Unit"))
    elif parser == "jasminer":
        summary = data.get("jasminer_status", {}).get("summary", {})
        if isinstance(summary, list):
            summary = summary[0] if summary else {}
        snapshot.rate, snapshot.rate_unit = explicit_rate(summary.get("rt"))
        snapshot.average_rate = explicit_rate(summary.get("avg"))[0]
        snapshot.uptime_seconds = number(summary.get("uptime"))
    elif parser == "elphapex":
        stats = block(data.get("elphapex_stats"), "STATS")
        chains = stats.get("chain", [])
        rates = [number(c.get("hashrate", c.get("rate_real"))) for c in chains if isinstance(c, dict)]
        real = sum(rates) if rates and all(v is not None for v in rates) else number(stats.get("rate_5s"))
        snapshot.rate = explicit_rate(real, "MH/s")[0]
        snapshot.average_rate = explicit_rate(stats.get("rate_avg"), "MH/s")[0]
        snapshot.uptime_seconds = number(stats.get("elapsed"))
        snapshot.fan_rpm = [v for v in (number(fan) for fan in stats.get("fan", [])) if v is not None]
    elif parser == "whatsminer":
        msg = data.get("rpc_summary", {}).get("msg", {})
        summary = msg.get("summary", msg) if isinstance(msg, dict) else {}
        if isinstance(summary, dict):
            snapshot.rate = rate_from_fields(summary)
            snapshot.average_rate = rate_from_fields(summary, True)
            for name, destination in (("hash-realtime", "rate"), ("hash-average", "average_rate")):
                if getattr(snapshot, destination) is None:
                    # docs/whatsminer_official_api.md, get.miner.status summary:
                    # hash-realtime/hash-average are TH/s, regardless of magnitude.
                    value, unit = explicit_rate(summary.get(name), summary.get("hash-unit", "TH/s"))
                    setattr(snapshot, destination, value)
                    snapshot.rate_unit = unit
            snapshot.uptime_seconds = number(summary.get("elapsed", summary.get("Elapsed")))
            snapshot.fan_rpm = [v for v in (number(summary.get(k)) for k in ("fan-speed-in", "fan-speed-out")) if v is not None]
            temperatures = summary.get("board-temperature", summary.get("temperature", []))
            if isinstance(temperatures, list):
                snapshot.temperatures_c = [v for v in map(number, temperatures) if v is not None]
    if snapshot.algorithm == "Equihash" and snapshot.rate is not None:
        snapshot.rate = snapshot.average_rate = None
        snapshot.rate_unit = "Sol/s"
        snapshot.diagnostics.append("CGMiner rate field does not establish the Equihash unit; a versioned mapping is required")
    for name, mapping in (metrics or {}).items():
        value, unit = explicit_rate(value_at(data, mapping["path"]), mapping["unit"])
        setattr(snapshot, name, value)
        snapshot.rate_unit = unit
    if snapshot.rate is None:
        snapshot.diagnostics.append("Hashrate missing or source unit not established")
    state = str(row.get("Status", "Unknown"))
    snapshot.mining_state = {"Sleep": "stopped", "Starting": "starting", "WaitWork": "starting", "Init": "starting"}.get(state, "unknown")
    if snapshot.rate is not None and snapshot.rate > 0:
        snapshot.mining_state = "running"
    return snapshot


def format_rate(value, unit="H/s"):
    if value is None:
        return "—"
    scales = ((1e6, "MSol/s"), (1e3, "kSol/s"), (1, "Sol/s")) if unit == "Sol/s" else ((1e12, "TH/s"), (1e9, "GH/s"), (1e6, "MH/s"), (1e3, "KH/s"), (1, "H/s"))
    for scale, label in scales:
        if value >= scale or scale == 1:
            return f"{value / scale:.2f} {label}"
