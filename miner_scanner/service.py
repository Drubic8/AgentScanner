"""Discovery, cached polling and bounded streaming scans."""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import replace
from datetime import datetime
import logging
from ipaddress import IPv4Address
from threading import Event, RLock, Lock
import time

from .drivers import collect, make_record, query
from .profiles import ProfileRegistry
from .ranges import expand_ranges
from .repository import DeviceRepository
from .runtime import Cancelled, DeadlineExceeded, Operation, ScanOptions
from .transports import Transport

logger = logging.getLogger(__name__)


class ScannerService:
    def __init__(self, *, registry=None, repository=None, transport_factory=Transport, options=None):
        self.registry = registry or ProfileRegistry()
        self.repository = repository or DeviceRepository()
        self.transport_factory = transport_factory
        self.options = options or ScanOptions()
        self._guard = RLock()
        self._locks = {}
        self._records = {}
        self._metadata = {}
        self._credentials = {}
        self._default_credentials = None

    def lock_for(self, ip):
        with self._guard:
            return self._locks.setdefault(ip, RLock())

    def set_credentials(self, ip, credentials):
        """Explicit per-device credentials, held in memory for this session only."""
        with self._guard:
            self._credentials[ip] = credentials
            self._metadata.pop(ip, None)

    def credentials_for(self, ip):
        with self._guard:
            return self._credentials.get(ip, self._default_credentials)

    def set_default_credentials(self, credentials):
        """Explicit session-wide fallback used for discovery behind authentication."""
        with self._guard:
            self._default_credentials = credentials
            self._metadata.clear()

    def get_record(self, ip):
        with self._guard:
            record = self._records.get(ip)
        return record if record is not None else self.repository.get(ip)

    def _discover(self, transport):
        data = {}
        probes = [
            ("version", "cgminer", "version", True),
            ("stats", "cgminer", "stats", False),
            ("rpc_info", "rpc", "get.device.info", True),
            ("system", "http", "/cgi-bin/get_system_info.cgi", True),
            ("vnish_info", "http", "/api/v1/info", True),
            ("elphapex_stats", "http", "/cgi-bin/luci/stats.cgi", False),
            ("jasminer_status", "http_post", "/cgi-bin/minerStatus.cgi", False),
            ("summary", "cgminer", "summary", False),
            ("web_status", "html", "/cgi-bin/minerStatus.cgi", False),
        ]
        cg_unreachable = False
        for definition in probes:
            if definition[1] == "cgminer" and cg_unreachable:
                continue
            value = query(transport, definition)
            if definition[0] == "version" and value is None and transport.operation.errors:
                # An unsupported command is a JSON reply; it must not disable stats.
                last_error = transport.operation.errors[-1]
                cg_unreachable = any(kind in last_error for kind in ("ConnectionRefusedError", "TimeoutError", "OSError"))
            if value is not None:
                data[definition[0]] = value
            profile = self.registry.resolve(data)
            if profile and profile.priority > 0:
                return profile, data
        return self.registry.resolve(data), data

    def poll(self, ip, target_makes=None, *, cancel=None, options=None, force_identify=False):
        ip = str(IPv4Address(ip))
        options = options or self.options
        op = Operation(options, cancel if cancel is not None else Event())
        lock = self.lock_for(ip)
        while not lock.acquire(timeout=min(0.05, op.remaining())):
            pass
        try:
            previous = self.get_record(ip)
            profile = self.registry.by_id.get(previous.identity.profile_id) if previous else None
            valid = False
            if previous and profile and previous.identity.profile_version == profile.version:
                age = time.time() - datetime.fromisoformat(previous.identity.identified_at).timestamp()
                valid = not force_identify and not previous.telemetry.stale and age < options.profile_ttl
            with self.transport_factory(ip, op, self.credentials_for(ip)) as transport:
                if valid:
                    data, metadata = collect(profile, transport, metadata=self._metadata.get(ip), metadata_ttl=options.metadata_ttl)
                    current_profile = self.registry.resolve(data)
                    if not current_profile or current_profile.id != profile.id:
                        self._metadata.pop(ip, None)
                        return self._stale(previous, "PROFILE CHANGED")
                else:
                    profile, seed = self._discover(transport)
                    if profile is None:
                        return self._stale(previous, "NO PROFILE")
                    data, metadata = collect(profile, transport, seed=seed, metadata_ttl=options.metadata_ttl)
                    resolved = self.registry.resolve(data)
                    if resolved is None:
                        return self._stale(previous, "AMBIGUOUS PROFILE")
                    if resolved.id != profile.id:
                        profile = resolved
                        data, metadata = collect(profile, transport, seed=data, metadata_ttl=options.metadata_ttl)
                dynamic = [q[0] for q in profile.queries if not q[3]]
                if not any(key in data for key in dynamic) and not (not valid and data.get("config")):
                    return self._stale(previous, "NO TELEMETRY")
                record = make_record(profile, ip, data, previous)
                if valid and previous.identity.fingerprint == record.identity.fingerprint:
                    record.identity.identified_at = previous.identity.identified_at
                record.telemetry.diagnostics.extend(op.errors)
                record.display["ScanTime"] = round(time.monotonic() - op.started, 3)
                record.display["RequestCount"] = op.request_count
                with self._guard:
                    self._records[ip] = record
                    self._metadata[ip] = metadata
                self.repository.save(record)
                if target_makes is not None and profile.make not in target_makes and profile.make != "Unknown":
                    return None
                return record
        except Cancelled:
            raise
        except DeadlineExceeded:
            return self._stale(locals().get("previous"), "DEADLINE")
        finally:
            lock.release()

    def _stale(self, previous, reason):
        if previous is None:
            return None
        record = replace(previous, telemetry=replace(previous.telemetry, stale=True), display=dict(previous.display))
        record.display.update({"Status": "Unknown", "Error": reason})
        with self._guard:
            self._records[record.identity.ip] = record
        return record

    def scan(self, ranges, target_makes=None, *, exclusions=(), cancel=None, options=None, on_result=None, on_progress=None, on_error=None):
        options = options or self.options
        cancel = cancel if cancel is not None else Event()
        addresses = expand_ranges(ranges, exclusions=exclusions, max_addresses=options.max_addresses)
        targets = iter(addresses)
        results = []
        done_count = 0
        with ThreadPoolExecutor(max_workers=options.workers, thread_name_prefix="asic-scan") as executor:
            pending = {}

            def fill():
                while len(pending) < options.workers and not cancel.is_set():
                    ip = next(targets, None)
                    if ip is None:
                        break
                    pending[executor.submit(self.poll, ip, target_makes, cancel=cancel, options=options)] = ip

            fill()
            while pending:
                completed, _ = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
                for future in completed:
                    ip = pending.pop(future)
                    done_count += 1
                    try:
                        record = future.result()
                    except Cancelled:
                        continue
                    except Exception as exc:
                        if on_error:
                            on_error(ip, getattr(exc, "code", type(exc).__name__))
                        logger.warning("Scan failed for %s (%s)", ip, type(exc).__name__)
                        continue
                    finally:
                        if on_progress:
                            on_progress(done_count, len(addresses))
                    if record is not None:
                        results.append(record)
                        if on_result:
                            on_result(record)
                fill()
        return results

    def monitor(self, ranges, *, interval=30.0, discovery_interval=600.0, cancel=None, **kwargs):
        """Run in a caller-owned worker. Cycles never overlap or accumulate."""
        if interval <= 0 or discovery_interval <= 0:
            raise ValueError("Monitoring intervals must be positive")
        cancel = cancel if cancel is not None else Event()
        known = set()
        next_discovery = 0.0
        while not cancel.is_set():
            started = time.monotonic()
            discovering = started >= next_discovery
            scope = ranges if discovering else sorted(known)
            records = self.scan(scope, cancel=cancel, **kwargs)
            known.update(r.identity.ip for r in records)
            if discovering:
                next_discovery = started + discovery_interval
            cancel.wait(max(0.01, interval - (time.monotonic() - started)))


_default = None
_default_lock = Lock()


def default_service():
    global _default
    with _default_lock:
        if _default is None:
            _default = ScannerService()
        return _default
