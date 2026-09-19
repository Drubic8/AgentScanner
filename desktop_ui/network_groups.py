"""Saved scan groups and bounded validation; no Qt, file writes or network calls."""
from dataclasses import dataclass
import re

from miner_scanner.ranges import expand_ranges, parse_range

MAX_ADDRESSES = 4096


def normalize_groups(data):
    """Read old list/dictionary formats without losing saved addresses."""
    if isinstance(data, dict):
        data = [{"name": name, "ranges": ranges} for name, ranges in data.items()]
    result = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        ranges = item.get("ranges", [item["range"]] if "range" in item else [])
        if isinstance(ranges, str):
            ranges = split_ranges(ranges)
        if isinstance(ranges, list) and all(isinstance(value, str) for value in ranges):
            result.append({"name": str(item.get("name", "Сеть")), "ranges": list(ranges),
                           "enabled": item.get("enabled") is not False})
    return result


def split_ranges(text):
    return list(dict.fromkeys(value.strip() for value in re.split(r"[\n,;]+", text) if value.strip()))


@dataclass(frozen=True)
class RangePreview:
    ranges: list[str]
    addresses: list[str]
    repeated: int


def preview_ranges(text):
    if len(text) > 200_000:
        raise ValueError("Слишком большой список. Разделите адреса на несколько сетей.")
    values = split_ranges(text)
    if not values:
        raise ValueError("Добавьте хотя бы один IP-адрес или диапазон.")
    addresses = set()
    total = 0
    for number, line in enumerate(text.splitlines(), 1):
        for value in (part.strip() for part in re.split(r"[,;]", line) if part.strip()):
            try:
                parsed = parse_range(value, MAX_ADDRESSES)
            except ValueError as exc:
                raise ValueError(f"Строка {number}: {value}\nПроверьте IPv4, маску или границы диапазона. {exc}") from exc
            total += len(parsed)
            addresses.update(parsed)
            if len(addresses) > MAX_ADDRESSES:
                raise ValueError(f"В одной группе допускается не более {MAX_ADDRESSES} IP-адресов.")
    ordered = expand_ranges(values, max_addresses=MAX_ADDRESSES)
    return RangePreview(values, ordered, total - len(addresses))


def validate_name(name, existing_names=()):
    name = name.strip()
    if not name:
        raise ValueError("Укажите название сети, например «Площадка 1 · контейнер А».")
    if name.casefold() in {value.strip().casefold() for value in existing_names}:
        raise ValueError("Сеть с таким названием уже есть. Используйте другое название.")
    return name


def selected_ranges(groups):
    return [value for group in groups if group.get("enabled", True) for value in group.get("ranges", [])]
