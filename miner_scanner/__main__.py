"""Headless entry point usable without Qt or pandas."""
import argparse
import json


def main():
    parser = argparse.ArgumentParser(description="Локальный сканер ASIC")
    parser.add_argument("ranges", nargs="*", help="IPv4, CIDR или диапазон")
    parser.add_argument("--profiles", action="store_true", help="Показать реестр без сканирования")
    parser.add_argument("--workers", type=int, default=64)
    args = parser.parse_args()
    if args.profiles:
        from .profiles import ProfileRegistry
        print(json.dumps([p.id for p in ProfileRegistry().profiles], ensure_ascii=False, indent=2))
        return
    if not args.ranges:
        parser.error("Укажите диапазон либо --profiles")
    from .core import scan_network_range
    from .runtime import ScanOptions
    print(json.dumps(scan_network_range(args.ranges, options=ScanOptions(workers=args.workers)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
