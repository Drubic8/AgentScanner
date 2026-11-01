"""Project configured pool slots without active-pool reordering or credentials.

The first slot retains the public Pool/Worker column IDs. Array positions are
significant, including empty entries; CGMiner's explicit POOL indexes are
zero-based. Pool priority/status describes failover, never a slot number.
"""
from urllib.parse import urlsplit, urlunsplit


POOL_FIELDS = ("Pool", "Worker", "Pool2", "Worker2", "Pool3", "Worker3")


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _url(value):
    """Keep the address, excluding optional user:password authority fields."""
    value = _text(value)
    if not value:
        return ""
    try:
        has_scheme = "://" in value
        parts = urlsplit(value if has_scheme else "//" + value)
        # Never expose embedded credentials in a pool URL or persisted record.
        address = parts.netloc.rsplit("@", 1)[-1]
        cleaned = urlunsplit((parts.scheme, address, parts.path, parts.query, parts.fragment))
        return cleaned if has_scheme else cleaned.removeprefix("//")
    except ValueError:
        return ""


def _field(item, names):
    for key in names:
        if key in item:
            return _text(item[key])
    return ""


def _slots(entries):
    result = [None, None, None]
    for position, item in enumerate(entries):
        if not isinstance(item, dict):
            continue
        index = position
        for key in ("POOL", "pool_index", "index"):
            if key in item:
                index = item[key]
                break
        if isinstance(index, str) and len(index) <= 12 and index.isascii() and index.isdigit():
            index = int(index)
        if type(index) is not int or not 0 <= index < 3 or result[index] is not None:
            continue
        result[index] = (
            _url(_field(item, ("URL", "url"))),
            _field(item, ("User", "user", "account", "worker")),
        )
    return result


def _list(value, key):
    return value.get(key) if isinstance(value, dict) and isinstance(value.get(key), list) else None


def _vnish_pools(data):
    """Read the current PoolStats contract, excluding firmware service pools.

    VNish's id is a zero-based pool identifier, not array order. Unlike generic
    response dictionaries, these records explicitly label UserPool/DevFee/Refund
    (docs/vnish_openapi.json). Only a usable complete list replaces cached CGMiner
    metadata; an explicit empty list is a usable current configuration as well.
    """
    summary = data.get("vnish_summary")
    if not isinstance(summary, dict):
        return None
    for root in (summary.get("miner"), summary):
        entries = _list(root, "pools")
        if entries is None:
            continue
        if not all(isinstance(item, dict)
                   and type(item.get("id")) is int and item["id"] >= 0
                   and isinstance(item.get("url"), str)
                   and isinstance(item.get("user"), str)
                   and item.get("pool_type") in ("UserPool", "DevFee", "Refund")
                   for item in entries):
            continue
        return [{"POOL": item["id"], "URL": item["url"], "User": item["user"]}
                for item in entries if item["pool_type"] == "UserPool"]
    return None


def _source(data, parser):
    # Current configuration is preferable to cached CGMiner metadata and exists
    # even when the mining process is asleep. Empty slots remain empty.
    if parser in ("antminer", "pitbit"):
        configured = _list(data.get("config"), "pools")
        if configured is not None:
            return configured
    if parser == "elphapex":
        configured = _list(data.get("elphapex_config"), "pools")
        if configured is not None:
            return configured
    if parser == "whatsminer":
        reply = data.get("rpc_pools")
        if isinstance(reply, dict):
            for key in ("msg", "Msg"):
                pools = _list(reply.get(key), "pools")
                if pools is not None:
                    return pools
            pools = _list(reply, "POOLS")
            if pools is not None:
                return pools
    if parser == "jasminer":
        status = data.get("jasminer_status")
        root = status.get("pools") if isinstance(status, dict) else None
        pools = _list(root, "pool")
        if pools is not None:
            return pools
        if isinstance(root, list):
            return root
    if parser == "goldshell" and isinstance(data.get("goldshell_pools"), list):
        return data["goldshell_pools"]
    if parser == "vnish":
        pools = _vnish_pools(data)
        if pools is not None:
            return pools
    pools = _list(data.get("pools"), "POOLS")
    if pools is not None:
        return pools
    return None


def pool_columns(data, parser, legacy=None):
    """Return all six display fields using already-collected API responses.

    Only reviewed address/account fields are copied. Passwords, authorization
    tokens and arbitrary response dictionaries never enter the projection.
    """
    entries = _source(data, parser)
    if entries is None:
        legacy = legacy or {}
        slots = [(_url(legacy.get("Pool")), _text(legacy.get("Worker"))), None, None]
    else:
        slots = _slots(entries)
    values = {}
    for index, slot in enumerate(slots):
        suffix = "" if index == 0 else str(index + 1)
        values["Pool" + suffix], values["Worker" + suffix] = slot or ("", "")
    return values
