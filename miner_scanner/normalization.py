"""Numeric telemetry independent of display strings and magnitude heuristics."""
import math
import re
from .models import TelemetrySnapshot


UNITS = {"H/s": 1, "KH/s": 1e3, "MH/s": 1e6, "GH/s": 1e9, "TH/s": 1e12,
         "M/s": 1e6, "G/s": 1e9, "Sol/s": 1, "kSol/s": 1e3, "MSol/s": 1e6}

# Connector numbers vary even within the same model and firmware build.
# Learn a complete observed pair, then retain it for the same identified device.
STOCK_FAN_COUNTS = {"Z11": 2, "Z15": 2, "Z15E": 2}


def antminer_model_name(model):
    return re.sub(r"^antminer\s*", "", model.strip(), flags=re.I).upper()


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


def antminer_equihash_rate(data, model, average=False):
    """Stock Z-series compatibility, carried over from the pre-refactor parser.

    These firmwares reuse hash-rate field names for solutions. Never apply the
    GHS multiplier (or a magnitude heuristic) to them. Other models need an
    explicit profile metric mapping. Hardware/build acceptance remains separate.
    """
    model = antminer_model_name(model)
    multiplier = {"Z9": 1, "Z9 MINI": 1, "Z11": 1, "Z15": 1, "Z15E": 1,
                  "Z15 PRO": 1e3, "Z15+": 1e3}.get(model)
    if multiplier is None:
        return None
    suffixes = ("av",) if average else ("5s", "1m", "5m")
    for prefix in ("GHS", "MHS"):
        for suffix in suffixes:
            value = number(data.get(f"{prefix} {suffix}"))
            if value is not None:
                return value * multiplier
    return None


def sensor_numbers(value):
    """Read a scalar, array or dash-separated sensor tuple without losing zero."""
    if isinstance(value, (list, tuple)):
        return [v for part in value for v in sensor_numbers(part)]
    if isinstance(value, str) and re.fullmatch(r"\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)+", value.strip()):
        return [float(part) for part in value.strip().split("-")]
    numeric = number(value)
    return [] if numeric is None else [numeric]


def antminer_fan_channels(values, model, previous=()):
    available = [f"fan{i}" for i in range(1, 17) if f"fan{i}" in values]
    expected = STOCK_FAN_COUNTS.get(antminer_model_name(model))
    if expected is None:
        return available, False
    active = [key for key in available if (number(values[key]) or 0) > 0]
    cached = list(previous)
    valid_cached = (len(cached) == expected and len(set(cached)) == expected
                    and all(re.fullmatch(r"fan(?:[1-9]|1[0-6])", key) for key in cached))
    if valid_cached and not set(active).difference(cached):
        return cached, True
    if len(active) == expected:
        return active, True
    # On a first scan with a failed fan, the API cannot identify which zero is
    # a stopped connector and which is unused. Keep labelled raw channels.
    return available, False


def antminer_fan_readings(values, model, channels=None):
    if channels is None:
        channels, _ = antminer_fan_channels(values, model)
    return [number(values.get(key)) for key in channels]


def antminer_sensors(values, model="", channels=None):
    fans = [rpm for rpm in antminer_fan_readings(values, model, channels) if rpm is not None]
    temperatures = []
    for index in range(1, 17):
        # Prefer chip sensors; each board is represented by its hottest sensor.
        for key in (f"temp2_{index}", f"temp_chip{index}", f"temp{index}"):
            readings = sensor_numbers(values.get(key))
            if readings and max(readings) > 0:
                temperatures.append(max(readings))
                break
    return fans, temperatures


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


def vnish_rates(info, summary):
    """REST rates use InfoJson.hr_measure, not the CGMiner compatibility labels.

    Deprecated instant_hashrate/average_hashrate have a different scale. Never
    substitute them or infer the unit from the value or model name.
    """
    unit = info.get("hr_measure") if isinstance(info, dict) else None
    miner = summary.get("miner", {}) if isinstance(summary, dict) else {}
    if not isinstance(miner, dict) or unit not in UNITS or "Sol" in unit:
        return None, None
    return (explicit_rate(miner.get("hr_realtime"), unit)[0],
            explicit_rate(miner.get("hr_average"), unit)[0])


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
    if parser == "antminer":
        snapshot.fan_rpm, snapshot.temperatures_c = antminer_sensors(values, row.get("Model", ""), row.get("FanChannels"))
    if parser == "avalon":
        from .avalon_compatibility import module_values
        module = module_values(data.get('stats'))
        # GHSspd describes the module now; MHS 1m can remain positive after
        # soft-off. Zero is a real measurement and must not trigger fallback.
        for field in ('GHSspd', 'GHSmm'):
            current = explicit_rate(module.get(field, values.get(field)), 'GH/s')[0]
            if current is not None:
                snapshot.rate = current
                break
        if snapshot.average_rate is None:
            snapshot.average_rate = explicit_rate(module.get('GHSavg', values.get("GHSavg")), "GH/s")[0]
        elapsed = number(module.get('Elapsed'))
        if elapsed is not None:
            snapshot.uptime_seconds = elapsed
    elif parser == "vnish":
        snapshot.rate, snapshot.average_rate = vnish_rates(
            data.get("vnish_info", {}), data.get("vnish_summary", {}))
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
    if snapshot.algorithm == "Equihash":
        snapshot.rate = snapshot.average_rate = None
        snapshot.rate_unit = "Sol/s"
        if parser == "antminer":
            snapshot.rate = antminer_equihash_rate(values, row.get("Model", ""))
            snapshot.average_rate = antminer_equihash_rate(values, row.get("Model", ""), True)
        if snapshot.rate is None:
            snapshot.diagnostics.append("CGMiner rate field does not establish the Equihash unit; a versioned mapping is required")
    for name, mapping in (metrics or {}).items():
        value, unit = None, "Sol/s" if "Sol" in mapping["unit"] else "H/s"
        for path in (mapping["path"], *mapping.get("fallback_paths", [])):
            value, unit = explicit_rate(value_at(data, path), mapping["unit"])
            if value is not None:
                break
        setattr(snapshot, name, value)
        snapshot.rate_unit = unit
    if snapshot.rate is None:
        snapshot.diagnostics.append("Hashrate missing or source unit not established")
    state = str(row.get("Status", "Unknown"))
    snapshot.mining_state = {"Sleep": "stopped", "Stopping": "stopping",
                             "Starting": "starting", "WaitWork": "starting", "Init": "starting"}.get(state, "unknown")
    avalon_explicit_state = parser == 'avalon' and 'Status' in row
    if avalon_explicit_state and state == 'Running':
        snapshot.mining_state = 'running'
    if (not avalon_explicit_state and snapshot.mining_state not in ("stopped", "stopping")
            and snapshot.rate is not None and snapshot.rate > 0):
        snapshot.mining_state = "running"
    return snapshot


def format_rate(value, unit="H/s", algorithm=None):
    if value is None:
        return "—"
    preferred = {"SHA-256": "TH/s", "Scrypt": "GH/s", "X11": "GH/s",
                 "Equihash": "kSol/s", "Etchash": "MH/s", "kHeavyHash": "TH/s"}.get(algorithm)
    if preferred and (unit == "Sol/s") == ("Sol" in preferred):
        return f"{value / UNITS[preferred]:.2f} {preferred}"
    scales = ((1e6, "MSol/s"), (1e3, "kSol/s"), (1, "Sol/s")) if unit == "Sol/s" else ((1e12, "TH/s"), (1e9, "GH/s"), (1e6, "MH/s"), (1e3, "KH/s"), (1, "H/s"))
    for scale, label in scales:
        if value >= scale or scale == 1:
            return f"{value / scale:.2f} {label}"
