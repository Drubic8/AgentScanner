"""Synthetic request-count benchmark. Never accesses ASICs or the network."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from miner_scanner.repository import DeviceRepository
from miner_scanner.runtime import ScanOptions
from miner_scanner.service import ScannerService
from tests.fakes import FakeFactory, FakeTransport


class DelayedTransport(FakeTransport):
    def value(self, key):
        self.operation.pause(self.factory.latency)
        return super().value(key)

    def http(self, *args, **kwargs):
        self.operation.pause(self.factory.latency)
        return super().http(*args, **kwargs)


class DelayedFactory(FakeFactory):
    def __init__(self, latency):
        super().__init__()
        self.latency = latency

    def __call__(self, ip, operation, credentials=None):
        return DelayedTransport(self, ip, operation, credentials)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--devices", type=int, default=128)
    parser.add_argument("--latency-ms", type=float, default=5)
    args = parser.parse_args()
    if not 1 <= args.devices <= 4096 or args.latency_ms <= 0:
        parser.error("Expected 1–4096 devices and positive latency")
    import ipaddress
    start = int(ipaddress.IPv4Address("192.0.2.1"))
    addresses = [str(ipaddress.IPv4Address(start + i)) for i in range(args.devices)]
    factory = DelayedFactory(args.latency_ms / 1000)
    repository = DeviceRepository(":memory:")
    service = ScannerService(repository=repository, transport_factory=factory, options=ScanOptions(workers=32))
    output = {"kind": "synthetic", "devices": args.devices, "workers": 32, "latency_ms": args.latency_ms}
    try:
        for mode in ("discovery", "cached_poll"):
            factory.calls.clear()
            started = time.perf_counter()
            records = service.scan(addresses)
            output[mode] = {"seconds": round(time.perf_counter() - started, 4), "requests": len(factory.calls), "records": len(records)}
            if len(records) != args.devices:
                raise RuntimeError("Benchmark lost devices")
    finally:
        repository.close()
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
