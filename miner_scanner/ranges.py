"""Validated IPv4 ranges. Expansion is bounded before allocation."""
from ipaddress import IPv4Address, ip_network


def parse_range(value: str, max_addresses=4096) -> list[str]:
    value = value.strip()
    if not value:
        raise ValueError("Пустой диапазон IP")
    if "/" in value:
        network = ip_network(value, strict=False)
        if network.version != 4:
            raise ValueError("Поддерживается только IPv4")
        count = network.num_addresses if network.prefixlen >= 31 else network.num_addresses - 2
        if count > max_addresses:
            raise ValueError(f"Диапазон превышает лимит {max_addresses} адресов")
        return [str(ip) for ip in network.hosts()]
    if "-" in value:
        start, end = (part.strip() for part in value.split("-", 1))
        if "." not in end:
            end = start.rsplit(".", 1)[0] + "." + end
        first, last = int(IPv4Address(start)), int(IPv4Address(end))
        if last < first:
            raise ValueError("Конец диапазона меньше начала")
        if last - first + 1 > max_addresses:
            raise ValueError(f"Диапазон превышает лимит {max_addresses} адресов")
        return [str(IPv4Address(ip)) for ip in range(first, last + 1)]
    return [str(IPv4Address(value))]


def expand_ranges(ranges, *, exclusions=(), max_addresses=4096):
    if isinstance(ranges, str):
        ranges = [ranges]
    targets = set()
    for item in ranges:
        targets.update(parse_range(item, max_addresses))
        if len(targets) > max_addresses:
            raise ValueError(f"Задание превышает лимит {max_addresses} адресов")
    if isinstance(exclusions, str):
        exclusions = [exclusions]
    for item in exclusions:
        targets.difference_update(parse_range(item, max_addresses))
    return sorted(targets, key=IPv4Address)
