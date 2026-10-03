"""Stock Bitmain presentation, using the same numeric telemetry as the service."""
import ipaddress
import re

from ..normalization import antminer_fan_readings, block, format_rate, normalize, number
from ..profiles import first_field
from ..utils import get_uptime_str


def algorithm_for(model, data):
    # Algorithms are properties of the model, not of the CGMiner field name.
    name = re.sub(r"^antminer\s*", "", model.strip(), flags=re.I).upper()
    for prefix, algorithm in (("Z", "Equihash"), ("D", "X11"), ("L", "Scrypt"),
                              ("E", "Etchash"), ("KS", "kHeavyHash"),
                              ("KA", "Blake2S"), ("K", "Eaglesong"),
                              ("S", "SHA-256"), ("T", "SHA-256")):
        if re.match(re.escape(prefix) + r"\d", name):
            return algorithm
    hint = first_field(data, {"algo", "algorithm"}) or ""
    for marker, algorithm in (("SHA", "SHA-256"), ("BTC", "SHA-256"),
                              ("SCRYPT", "Scrypt"), ("LTC", "Scrypt"),
                              ("X11", "X11"), ("DASH", "X11"),
                              ("EQUIHASH", "Equihash"), ("ZEC", "Equihash"),
                              ("HEAVY", "kHeavyHash"), ("ETH", "Etchash")):
        if marker in hint.upper():
            return algorithm
    return hint or "Unknown"


def stock_model(resp):
    """Prefer the system model over CGMiner's generic family label.

    KS5 Pro reports KS5 in CGMiner while system.minertype names the variant.
    Probe completion order must not make it appear to be a different device.
    """
    system = resp.get("system")
    if isinstance(system, dict):
        for key in ("minertype", "model"):
            value = system.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return first_field(resp, {"model", "minertype", "product_type", "type", "g-model"})


def parse_antminer_stock(ip, resp, diagnostics=("", ""), work_mode=None):
    raw_model = stock_model(resp)
    model = raw_model or "Antminer Unknown"
    if raw_model and not model.lower().startswith("antminer"):
        model = f"Antminer {model}"
    row = {"IP": ip, "Make": "Bitmain", "Model": model,
           "Algo": algorithm_for(model, resp), "SortIP": int(ipaddress.IPv4Address(ip))}
    snapshot = normalize(resp, row, "antminer")
    stats = block(resp.get("stats"), "STATS")
    config = resp.get("config")
    config = config if isinstance(config, dict) else {}
    if work_mode is None:
        work_mode = config.get("bitmain-work-mode")
        if work_mode is None:
            work_mode = config.get("miner-mode")

    failed = [str(i) for i in range(1, 17)
              if re.search(r"[x-]", str(stats.get(f"chain_acs{i}", "")), re.I)]
    short_error, detail_error = diagnostics
    if failed:
        boards = ",".join(failed)
        short_error = " + ".join(filter(None, (f"HW ERR (B{boards})", short_error)))
        detail_error = "\n".join(filter(None, (f"API сообщает неработающие чипы на платах {boards}", detail_error)))

    if number(work_mode) == 1:
        status = "Sleep"
        short_error, detail_error = diagnostics
    elif snapshot.rate is not None and snapshot.rate > 0:
        status = "Running"
    elif short_error:
        status = "Error"
    elif snapshot.rate == 0 and number(work_mode) == 0:
        status, short_error = "Error", "NO HASH"
        detail_error = "Майнинг включён в конфигурации, но текущий хешрейт равен 0."
    else:
        # Missing counters and inaccessible configuration prove neither fault nor sleep.
        status, short_error = "Unknown", "STATE UNCONFIRMED"
        detail_error = "Недостаточно данных для определения режима. Проверьте доступ к API и авторизацию."

    pools = resp.get("pools", {})
    pools = pools.get("POOLS", []) if isinstance(pools, dict) else []
    pool = next((p for p in pools if isinstance(p, dict)), {})
    if not pool:
        configured = config.get("pools", [])
        pool = next((p for p in configured if isinstance(p, dict)), {})
    row.update({
        "Status": status, "Error": short_error, "ErrorDetails": detail_error,
        "Uptime": get_uptime_str(snapshot.uptime_seconds) if snapshot.uptime_seconds is not None else "—",
        "Real": format_rate(snapshot.rate, snapshot.rate_unit, snapshot.algorithm),
        "Avg": format_rate(snapshot.average_rate, snapshot.rate_unit, snapshot.algorithm),
        "RawHash": snapshot.rate,
        "Fan": " ".join("—" if v is None else f"{v:g}" for v in antminer_fan_readings(
            {**stats, **block(resp.get("summary"), "SUMMARY")}, model)),
        "Temp": " ".join(f"{v:g}" for v in snapshot.temperatures_c),
        "Pool": pool.get("URL", pool.get("url", "")),
        "Worker": pool.get("User", pool.get("user", "")),
    })
    return row
