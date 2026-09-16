"""Read-only, bounded diagnostic collection: python -m miner_scanner.diagnostics."""
import argparse
from dataclasses import asdict
from getpass import getpass
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import re
import signal
from threading import Event, RLock
import time
from zipfile import ZipFile, ZIP_DEFLATED

from .models import Credentials, utc_now
from .ranges import expand_ranges
from .repository import DeviceRepository
from .runtime import ScanOptions
from .service import ScannerService
from .transports import Transport

# Explicitly reviewed reads. A future profile cannot silently enable a write here.
HTTP_READS = {
    "/cgi-bin/get_system_info.cgi", "/cgi-bin/get_miner_conf.cgi",
    "/api/v1/info", "/api/v1/summary", "/cgi-bin/luci/stats.cgi",
    "/cgi-bin/luci/get_miner_conf.cgi", "/cgi-bin/luci/get_system_info.cgi",
    "/cgi-bin/minerStatus.cgi", "/cgi-bin/minerConfiguration.cgi", "/warning",
}
SENSITIVE = re.compile(r"pass|pwd|secret|token|auth|cookie|salt|sign|user|worker|wallet|pool|url|host|serial|(^|_)sn$|mac|address|fingerprint|device.?id|(^|_)ip$", re.I)
SAFE_TEXT = {"model", "minertype", "type", "make", "firmware", "firmwareversion",
             "firmware_version", "api_version", "version", "compiletime", "compile_time",
             "product_type", "g-model", "profile_id", "algorithm", "rate_unit", "mining_state"}


def redact(value, key="", secrets=()):
    """Fail closed for unknown strings; retain JSON shape and numeric telemetry."""
    if SENSITIVE.search(key):
        return "[redacted]"
    if isinstance(value, dict):
        # Dynamic keys can themselves contain a username, URL or address.
        return {k if re.fullmatch(r"[A-Za-z][A-Za-z0-9 _%-]{0,63}", str(k))
                and not any(secret and secret in str(k) for secret in secrets) else f"field_{i}":
                redact(v, str(k), secrets) for i, (k, v) in enumerate(value.items())}
    if isinstance(value, list):
        return [redact(v, key, secrets) for v in value]
    if isinstance(value, str):
        if any(secret and secret in value for secret in secrets):
            return "[redacted]"
        if re.fullmatch(r"-?\d+(\.\d+)?", value):
            return value
        if key.lower() in SAFE_TEXT and len(value) <= 160:
            if not re.search(r"(?:\d{1,3}\.){3}\d{1,3}|(?:[0-9a-f]{2}:){5}|://|@", value, re.I):
                return value
        return "[text omitted]"
    return value


class Capture:
    def __init__(self, addresses, secrets=(), max_bytes=32 * 1024 * 1024):
        self.aliases = {ip: f"device-{i:03}" for i, ip in enumerate(addresses, 1)}
        self.secrets, self.max_bytes = secrets, max_bytes
        self.events, self.size, self.dropped = [], 0, 0
        self.lock = RLock()

    def add(self, ip, kind, **data):
        item = {"at": utc_now(), "device": self.aliases[ip], "kind": kind, **data}
        encoded = json.dumps(item, ensure_ascii=False)
        size = len(encoded.encode("utf-8"))
        with self.lock:
            if self.size + size > self.max_bytes:
                self.dropped += 1
            else:
                self.events.append(encoded)
                self.size += size

    def save(self, output, manifest):
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        # Never overwrite a previous run's evidence.
        with ZipFile(output, "x", ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps({"schema": 1, "read_only": True,
                "redaction": "unknown text and sensitive fields omitted; no raw HTML",
                "events_dropped": self.dropped, **manifest}, ensure_ascii=False, indent=2))
            archive.writestr("events.jsonl", "\n".join(self.events))


class RecordingTransport:
    def __init__(self, ip, operation, credentials, capture, factory=Transport):
        self.ip, self.operation, self.capture = ip, operation, capture
        self.inner = factory(ip, operation, credentials)

    def __enter__(self):
        self.inner.__enter__()
        return self

    def __exit__(self, *args):
        return self.inner.__exit__(*args)

    def _call(self, protocol, command, *args, **kwargs):
        allowed = (
            protocol == "cgminer" and command in {"version", "stats", "summary", "pools"}
            or protocol == "rpc" and (command, args[0] if args else None) in {
                ("get.device.info", None), ("get.miner.status", "pools"), ("get.miner.status", "summary")}
            or protocol in {"http", "http_json"} and command in HTTP_READS
            and (args[0] if args else "GET") in ({"GET", "POST"} if command == "/cgi-bin/minerStatus.cgi" else {"GET"})
        )
        if not allowed or any(k in kwargs for k in ("payload", "headers", "raw")):
            raise ValueError("Diagnostic collector rejected a non-read request")
        started = time.monotonic()
        info = {"protocol": protocol, "query": command}
        if protocol == "rpc":
            info["parameter"] = args[0] if args else None
        try:
            result = getattr(self.inner, protocol)(command, *args, **kwargs)
            if protocol == "http":
                status, body = result
                info.update(http_status=status, body_bytes=len(body), body="[non-JSON body omitted]")
            else:
                info["response"] = redact(result, secrets=self.capture.secrets)
            return result
        except Exception as exc:
            info["error"] = type(exc).__name__  # Exception messages may contain credentials.
            raise
        finally:
            info["duration_ms"] = round((time.monotonic() - started) * 1000, 2)
            self.capture.add(self.ip, "request", **info)

    def cgminer(self, command, **kwargs):
        return self._call("cgminer", command, **kwargs)

    def rpc(self, command, parameter=None, **kwargs):
        return self._call("rpc", command, parameter, **kwargs)

    def http(self, path, method="GET", **kwargs):
        return self._call("http", path, method, **kwargs)

    def http_json(self, path, method="GET", **kwargs):
        return self._call("http_json", path, method, **kwargs)


def collect_diagnostics(addresses, *, samples, interval, options, credentials=None, cancel=None, factory=Transport):
    cancel = cancel or Event()
    secrets = (credentials.username, credentials.password) if credentials else ()
    capture = Capture(addresses, secrets)
    repository = DeviceRepository(":memory:")
    service = ScannerService(repository=repository, options=options,
        transport_factory=lambda ip, op, creds: RecordingTransport(ip, op, creds, capture, factory))
    service.set_default_credentials(credentials)
    manifest = {"started_at": utc_now(), "targets": len(addresses), "requested_samples": samples,
                "completed_samples": 0, "records": 0, "fresh_records": 0,
                "interval_after_scan_seconds": interval, "options": asdict(options)}
    manifest["collector_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    manifest["profiles_sha256"] = hashlib.sha256((Path(__file__).parent / "profiles" / "catalog.json").read_bytes()).hexdigest()
    try:
        manifest["package_version"] = version("asic-monitor-scanner")
    except PackageNotFoundError:
        manifest["package_version"] = "source checkout; see collector_sha256"
    try:
        for sample in range(samples):
            if cancel.is_set():
                break
            started = time.monotonic()
            def record(item):
                manifest["records"] += 1
                manifest["fresh_records"] += int(not item.telemetry.stale)
                capture.add(item.identity.ip, "record", sample=sample + 1,
                            data=redact(asdict(item), secrets=secrets))
            service.scan(addresses, cancel=cancel, on_result=record,
                on_error=lambda ip, code: capture.add(ip, "scan_error", error=redact(str(code))))
            manifest["completed_samples"] += int(not cancel.is_set())
            manifest["last_scan_seconds"] = round(time.monotonic() - started, 3)
            print(f"Sample {sample + 1}/{samples}; records: {manifest['records']}")
            if sample + 1 < samples and cancel.wait(interval):
                break
    finally:
        repository.close()
    manifest.update(finished_at=utc_now(), cancelled=cancel.is_set())
    return capture, manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ranges", nargs="+")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--samples", type=int, default=1)
    parser.add_argument("--interval", type=float, default=30)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--username")
    parser.add_argument("--ask-password", action="store_true")
    parser.add_argument("--auth", choices=("digest", "basic"), default="digest")
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("Output already exists; choose a new ZIP filename")
    if not 1 <= args.samples <= 2880 or not 1 <= args.interval <= 86400:
        parser.error("Use 1..2880 samples and an interval of 1..86400 seconds")
    if bool(args.username) != args.ask_password:
        parser.error("Use --username and --ask-password together")
    try:
        addresses = expand_ranges(args.ranges, max_addresses=4096)
        options = ScanOptions(workers=args.workers)
    except ValueError as exc:
        parser.error(str(exc))
    credentials = Credentials(args.username, getpass("ASIC password: "), args.auth) if args.username else None
    cancel = Event()
    previous = signal.signal(signal.SIGINT, lambda *_: cancel.set())
    try:
        capture, manifest = collect_diagnostics(addresses, samples=args.samples, interval=args.interval,
            options=options, credentials=credentials, cancel=cancel)
        capture.save(args.output, manifest)
    finally:
        signal.signal(signal.SIGINT, previous)
    print(f"Saved: {args.output}; omitted events: {capture.dropped}")
    return 0 if manifest["fresh_records"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
