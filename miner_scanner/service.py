"""Discovery, cached polling and bounded streaming scans."""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import replace
from datetime import datetime
import logging
from ipaddress import IPv4Address
from threading import Event, RLock, Lock
import time

from .drivers import collect, make_record, query
from .access import access_family
from .profiles import ProfileRegistry
from .ranges import expand_ranges
from .repository import DeviceRepository
from .runtime import Cancelled, DeadlineExceeded, Operation, ScanOptions
from .transports import Transport
from . import stock_compatibility
from . import whatsminer_compatibility
from . import vnish_compatibility
from . import avalon_compatibility
from . import elphapex_compatibility

logger = logging.getLogger(__name__)


class ScannerService:
    def __init__(self, *, registry=None, repository=None, transport_factory=Transport, options=None):
        self.registry = registry or ProfileRegistry()
        self.repository = repository or DeviceRepository()
        self.transport_factory = transport_factory
        self.options = options or ScanOptions()
        self._guard = RLock()
        self._locks = {}
        self._command_locks = {}
        self._records = {}
        self._metadata = {}
        self._credentials = {}
        self._default_credentials = None
        self.access_profiles = None
        self._attempt_credentials = {}
        self._resolved_credentials = {}
        self._last_auth_failed = {}
        self._last_poll_credentials = {}
        self._interface_cache = {}
        self._control_contracts = {}

    def set_access_profiles(self, profiles):
        with self._guard:
            self.access_profiles = profiles
            self._resolved_credentials.clear()
            self._metadata.clear()
            self._interface_cache.clear()
            self._control_contracts.clear()

    def credential_candidates(self, ip, family=None):
        with self._guard:
            explicit = self._credentials.get(ip, self._default_credentials)
            profiles = self.access_profiles
            resolved = self._resolved_credentials.get(ip)
        if explicit is not None:
            return [explicit]
        if profiles is None:
            return []
        if family is None:
            record = self.get_record(ip)
            family = access_family(record.identity.profile_id) if record else None
        candidates = profiles.candidates(ip, family)
        if resolved in candidates:
            candidates.remove(resolved)
            candidates.insert(0, resolved)
        return candidates

    def remember_credentials(self, ip, credentials):
        with self._guard:
            self._resolved_credentials[ip] = credentials

    def lock_for(self, ip):
        with self._guard:
            return self._locks.setdefault(ip, RLock())

    def command_lock_for(self, ip):
        """A command keeps ownership through dispatch and deferred verification."""
        with self._guard:
            return self._command_locks.setdefault(ip, Lock())

    def set_credentials(self, ip, credentials):
        """Explicit per-device credentials, held in memory for this session only."""
        with self._guard:
            self._credentials[ip] = credentials
            self._metadata.pop(ip, None)
            self._interface_cache.pop(ip, None)
            self._control_contracts.pop(ip, None)

    def credentials_for(self, ip):
        with self._guard:
            if ip in self._attempt_credentials:
                return self._attempt_credentials[ip]
        candidates = self.credential_candidates(ip)
        return candidates[0] if candidates else None

    def set_default_credentials(self, credentials):
        """Explicit session-wide fallback used for discovery behind authentication."""
        with self._guard:
            self._default_credentials = credentials
            self._metadata.clear()
            self._interface_cache.clear()
            self._control_contracts.clear()

    def get_record(self, ip):
        with self._guard:
            record = self._records.get(ip)
        return record if record is not None else self.repository.get(ip)

    def _discover(self, transport):
        data = {}
        probes = [
            ("version", "cgminer", "version", True),
            ("stats", "cgminer", "stats", False, {"repair": "antminer_stats"}),
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
        if self.access_profiles is None:
            return self._poll_once(ip, target_makes, cancel=cancel, options=options, force_identify=force_identify)
        ip = str(IPv4Address(ip))
        options = options or self.options
        budget = Operation(options, cancel if cancel is not None else Event())
        lock = self.lock_for(ip)
        while not lock.acquire(timeout=min(0.05, budget.remaining())):
            pass
        try:
            candidates = self.credential_candidates(ip) or [None]
            record = self.get_record(ip)
            for credentials in candidates:
                self._attempt_credentials[ip] = credentials
                if self._last_poll_credentials.get(ip) != credentials:
                    self._metadata.pop(ip, None)
                self._last_poll_credentials[ip] = credentials
                record = self._poll_once(ip, target_makes, cancel=cancel,
                                         options=replace(options, device_timeout=budget.remaining()),
                                         force_identify=force_identify)
                if not self._last_auth_failed.get(ip):
                    if credentials is not None and record is not None and not record.telemetry.stale:
                        self.remember_credentials(ip, credentials)
                    break
                # VNish authenticates via unlock/Bearer, not HTTP credential probing.
                if record and record.identity.profile_id == 'bitmain.vnish':
                    break
            return record
        except DeadlineExceeded:
            return self._stale(self.get_record(ip), "DEADLINE")
        finally:
            self._attempt_credentials.pop(ip, None)
            lock.release()

    def _poll_once(self, ip, target_makes=None, *, cancel=None, options=None, force_identify=False):
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
                        if any(error.endswith(":auth_required") for error in op.errors):
                            record = self._stale(previous, "AUTH REQUIRED")
                            if record is not None:
                                record.display["ErrorDetails"] = (
                                    "Не удалось определить устройство: HTTP API требует авторизацию. "
                                    "Настройте логин, пароль и Digest/Basic в доступе к ASIC, затем повторите сканирование. "
                                    "Отображаются ранее сохранённые данные; текущее состояние не подтверждено.")
                                record.telemetry.diagnostics = [*record.telemetry.diagnostics, *op.errors]
                            return record
                        return self._stale(previous, "NO PROFILE")
                    data, metadata = collect(profile, transport, seed=seed, metadata_ttl=options.metadata_ttl)
                    resolved = self.registry.resolve(data)
                    if resolved is None:
                        return self._stale(previous, "AMBIGUOUS PROFILE")
                    if resolved.id != profile.id:
                        additional_queries = tuple(q for q in resolved.queries if q not in profile.queries)
                        profile = resolved
                        # Refining a family into an exact build must not repeat
                        # the same failed reads (or authentication challenges).
                        data, metadata = collect(replace(profile, queries=additional_queries), transport,
                                                 seed=data, metadata=metadata, metadata_ttl=options.metadata_ttl)
                config_timeouts = {"config:ReadTimeout", "config:TimeoutError", "config:ConnectTimeout"}
                if profile.parser == "antminer" and "config" not in data and config_timeouts.intersection(op.errors):
                    # One bounded read retry: slow Digest/CGI must not turn sleep
                    # into Unknown. Never retry a write or guess sleep from zero.
                    try:
                        if op.remaining() > 1:
                            value = query(transport, ("config", "http", "/cgi-bin/get_miner_conf.cgi", False,
                                                     {"read_timeout": max(4.0, options.read_timeout)}))
                            if value is not None:
                                data["config"] = value
                    except DeadlineExceeded:
                        pass
                dynamic = [q[0] for q in profile.queries if not q[3]]
                if not any(key in data for key in dynamic) and not (not valid and data.get("config")):
                    return self._stale(previous, "NO TELEMETRY")
                record = make_record(profile, ip, data, previous)
                self._control_contracts.pop(ip, None)
                if stock_compatibility.eligible(record):
                    cached = self._interface_cache.get(ip)
                    if (not force_identify and cached and cached[0] == record.identity.fingerprint
                            and time.monotonic() - cached[1] < options.metadata_ttl):
                        evidence = cached[2]
                    else:
                        evidence = stock_compatibility.probe(transport)
                        self._interface_cache[ip] = (record.identity.fingerprint, time.monotonic(), evidence)
                    compatibility = stock_compatibility.resolve(record, data.get('config'), evidence)
                    self._control_contracts[ip] = compatibility
                    stock_compatibility.apply_capabilities(record, profile, compatibility)
                elif profile.control == 'vnish':
                    cached = self._interface_cache.get(ip)
                    if (not force_identify and cached and cached[0] == record.identity.fingerprint
                            and time.monotonic() - cached[1] < options.metadata_ttl):
                        evidence = cached[2]
                    else:
                        evidence = vnish_compatibility.probe(transport)
                        self._interface_cache[ip] = (record.identity.fingerprint, time.monotonic(), evidence)
                    compatibility = vnish_compatibility.resolve(data.get('vnish_info'), evidence)
                    self._control_contracts[ip] = compatibility
                    stock_compatibility.apply_capabilities(record, profile, compatibility)
                elif profile.control == 'elphapex':
                    cached = self._interface_cache.get(ip)
                    if (not force_identify and cached and cached[0] == record.identity.fingerprint
                            and time.monotonic() - cached[1] < options.metadata_ttl):
                        evidence = cached[2]
                    else:
                        evidence = elphapex_compatibility.probe(transport)
                        self._interface_cache[ip] = (record.identity.fingerprint, time.monotonic(), evidence)
                    compatibility = elphapex_compatibility.resolve(record, evidence, data.get('elphapex_config'))
                    self._control_contracts[ip] = compatibility
                    stock_compatibility.apply_capabilities(record, profile, compatibility)
                elif profile.control == 'avalon':
                    compatibility = avalon_compatibility.resolve(transport, data)
                    self._control_contracts[ip] = compatibility
                    stock_compatibility.apply_capabilities(record, profile, compatibility)
                elif profile.control == 'whatsminer':
                    compatibility = whatsminer_compatibility.resolve(data.get('rpc_info'))
                    self._control_contracts[ip] = compatibility
                    stock_compatibility.apply_capabilities(record, profile, compatibility)
                from .identify import read_state
                record.telemetry.identify_enabled = read_state(profile.control, transport, data,
                                                               compatibility=self._control_contracts.get(ip))
                if (profile.parser == "antminer" and record.display.get("Status") == "Unknown"
                        and record.display.get("Error") == "STATE UNCONFIRMED"
                        and "config:auth_required" in op.errors):
                    record.display["Error"] = "AUTH REQUIRED"
                    record.display["ErrorDetails"] = (
                        "API требует авторизацию для чтения режима работы. "
                        "Настройте доступ к ASIC (логин, пароль и Digest/Basic) и повторите сканирование. "
                        "Нулевой хешрейт без конфигурации не подтверждает сон или неисправность.")
                if (profile.parser == "antminer" and record.display.get("Error") == "STATE UNCONFIRMED"
                        and "config" not in data and config_timeouts.intersection(op.errors)):
                    record.display["Error"] = "CONFIG TIMEOUT"
                    record.display["ErrorDetails"] = (
                        "ASIC ответил, но режим работы не получен за отведённое время. "
                        "Повторное чтение также ограничено общим временем опроса. Сон не подтверждён. Повторите сканирование или увеличьте "
                        "ожидание ответа в настройках сканирования.")
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
            self._last_auth_failed[ip] = any(error.endswith(':auth_required') for error in op.errors)
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
