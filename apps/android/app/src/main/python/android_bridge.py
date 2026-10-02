"""Android boundary: bounded worker, JSON DTOs, no Android imports or credentials on disk."""
from dataclasses import asdict
from concurrent.futures import ThreadPoolExecutor
import csv
import io
import json
from pathlib import Path
import re
from threading import Event, RLock, Thread

from miner_scanner.access import AccessProfiles, standard_profiles
from miner_scanner.commands import ALIASES, execute_command
from miner_scanner.models import Credentials
from miner_scanner.normalization import format_rate
from miner_scanner.ranges import expand_ranges
from miner_scanner.repository import DeviceRepository
from miner_scanner.runtime import ScanOptions
from miner_scanner.service import ScannerService


def parse_ranges(text):
    parts = [part for part in re.split(r"[\s,;]+", text.strip()) if part]
    if not parts:
        raise ValueError("Укажите IP, подсеть CIDR или диапазон адресов")
    return expand_ranges(parts, max_addresses=4096)


def validate_ranges(text):
    try:
        return json.dumps({"count": len(parse_ranges(text)), "error": ""}, ensure_ascii=False)
    except ValueError:
        return json.dumps({"count": 0, "error": "Проверьте IPv4-адреса. Максимум — 4096 адресов."}, ensure_ascii=False)


def csv_cell(value):
    text = "" if value is None else str(value)
    # Spreadsheet formula injection protection for data supplied by ASIC firmware.
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")) else text


MOBILE_ACTIONS = frozenset({"identify_on", "identify_off", "identify_toggle", "reboot",
                          "mining_stop", "mining_start", "low", "normal_power", "hem"})


def device_row(record):
    # Explicit DTO: never serialize raw replies, worker names, pools or credentials.
    telemetry = asdict(record.telemetry)
    telemetry.pop("diagnostics", None)
    if telemetry["stale"]:
        telemetry.update(rate=None, average_rate=None, temperatures_c=[], fan_rpm=[])
    return {"identity": asdict(record.identity), "telemetry": telemetry,
            "capabilities": dict(record.capabilities),
            "rate_display": format_rate(telemetry["rate"], telemetry["rate_unit"], telemetry["algorithm"])}


class MobileScanner:
    def __init__(self, data_dir, service=None):
        self.service = service or ScannerService(repository=DeviceRepository(Path(data_dir) / "devices.sqlite3"))
        if service is None:
            # Same bounded, firmware-specific defaults as desktop; no credential files.
            self.service.set_access_profiles(AccessProfiles(standard_profiles()))
        self.lock = RLock()
        self.cancel_event = Event()
        self.thread = None
        self.state = {"running": False, "cancelled": False, "processed": 0,
                      "total": 0, "errors": 0, "error": "", "rows": [], "command": None,
                      "commands": [], "command_total": 0, "operation": "scan"}

    def snapshot(self):
        with self.lock:
            return json.dumps(self.state, ensure_ascii=False, allow_nan=False)

    def start(self, ranges, username="", password="", auth="digest", workers=8):
        addresses = parse_ranges(ranges)  # Validate the whole input before any network activity.
        if auth not in {"basic", "digest"} or workers not in {4, 8, 16, 32}:
            raise ValueError("Invalid mobile scan settings")
        options = ScanOptions(workers=workers, device_timeout=12, connect_timeout=1, read_timeout=2)
        with self.lock:
            if self.state["running"]:
                raise ValueError("An operation is already running")
            self.service.set_default_credentials(Credentials(username, password, auth) if username or password else None)
            self.cancel_event = Event()
            self.state = {"running": True, "cancelled": False, "processed": 0,
                          "total": len(addresses), "errors": 0, "error": "", "rows": [], "command": None,
                          "commands": [], "command_total": 0, "operation": "scan"}
            self.thread = Thread(target=self._scan, args=(addresses, options), daemon=True, name="mobile-scan")
            self.thread.start()

    def _scan(self, addresses, options):
        def received(record):
            row = device_row(record)
            with self.lock:
                self.state["rows"].append(row)
        def progress(done, total):
            with self.lock:
                self.state.update(processed=done, total=total)
        def error(_ip, _code):
            with self.lock:
                self.state["errors"] += 1
        try:
            self.service.scan(addresses, options=options, cancel=self.cancel_event,
                              on_result=received, on_progress=progress, on_error=error)
        except Exception:
            with self.lock:
                self.state["error"] = "Сканирование прервано. Проверьте подключение и повторите."
        finally:
            with self.lock:
                self.state.update(running=False, cancelled=self.cancel_event.is_set())

    def cancel(self):
        self.cancel_event.set()

    def _target_error(self, ip, device_id, action):
        record = self.service.get_record(ip)
        if not record or record.identity.device_id != device_id or record.telemetry.stale:
            return "skipped", "Данные устройства изменились или устарели. Повторите сканирование."
        if record.capabilities.get(action) != "supported":
            return "unsupported", "Интерфейс устройства не подтверждает поддержку этой команды."
        return None

    def command(self, ip, device_id, action, username=None, password=None, auth="digest"):
        action = ALIASES.get(action, action)
        with self.lock:
            error = self._target_error(ip, device_id, action)
            if error:
                raise ValueError(error[1])
            self._start_commands([(ip, device_id)], action, username, password, auth)

    def command_many(self, targets_json, action, username=None, password=None, auth="digest"):
        targets = json.loads(targets_json)
        if not isinstance(targets, list) or not 1 <= len(targets) <= 4096:
            raise ValueError("Выберите от 1 до 4096 устройств")
        pairs = []
        for target in targets:
            if (not isinstance(target, dict) or set(target) != {"ip", "device_id"}
                    or not all(isinstance(value, str) and value for value in target.values())):
                raise ValueError("Invalid command target")
            # Accept single IPv4 addresses, never a subnet or range in a command.
            if parse_ranges(target["ip"]) != [target["ip"]]:
                raise ValueError("Invalid command address")
            pairs.append((target["ip"], target["device_id"]))
        if len({ip for ip, _ in pairs}) != len(pairs):
            raise ValueError("Duplicate command targets")
        self._start_commands(pairs, ALIASES.get(action, action), username, password, auth)

    def _start_commands(self, targets, action, username, password, auth):
        with self.lock:
            if self.state["running"]:
                raise ValueError("An operation is already running")
            if action not in MOBILE_ACTIONS or auth not in {"basic", "digest"}:
                raise ValueError("Invalid mobile command settings")
            visible = {(row["identity"]["ip"], row["identity"]["device_id"]) for row in self.state["rows"]}
            if any(target not in visible for target in targets):
                raise ValueError("Выберите устройства из текущего сканирования")
            if username is not None or password is not None:
                self.service.set_default_credentials(Credentials(username or "", password or "", auth)
                                                     if username or password else None)
            self.cancel_event = Event()
            self.state.update(running=True, command=None, commands=[], command_total=len(targets),
                              operation="command", cancelled=False, error="")
            self.thread = Thread(target=self._commands, args=(targets, action), daemon=True, name="mobile-command")
            self.thread.start()

    def _commands(self, targets, action):
        def run(target):
            ip, device_id = target
            result = {"ip": ip, "device_id": device_id, "action": action, "accepted_by_api": False}
            try:
                error = (("cancelled", "Операция отменена; команда не отправлена.")
                         if self.cancel_event.is_set() else self._target_error(ip, device_id, action))
                if error:
                    result.update(status=error[0], message=error[1])
                else:
                    # The shared executor freshly identifies the device and checks its API contract.
                    # No experimental override and no repeat after a lost response.
                    executed = execute_command(self.service, ip, action, device_id=device_id, cancel=self.cancel_event)
                    result.update(asdict(executed))
                    if (not self.cancel_event.is_set() and action != "reboot"
                            and (executed.accepted_by_api or executed.succeeded)):
                        self.service.poll(ip, cancel=self.cancel_event,
                                          options=ScanOptions(device_timeout=8, read_timeout=2, connect_timeout=1))
            except Exception:
                # A read-back failure must not replace an already recorded command outcome.
                if "status" not in result:
                    result.update(status="unconfirmed", message="Результат команды неизвестен. Проверьте ASIC; команда не повторяется.")
            finally:
                record = self.service.get_record(ip)
                with self.lock:
                    if record:
                        self.state["rows"] = [device_row(record) if row["identity"]["ip"] == ip else row
                                              for row in self.state["rows"]]
                    self.state["commands"].append(result)
                    self.state["command"] = result if len(targets) == 1 else None
        try:
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix="mobile-control") as executor:
                # Workers check cancellation before each write; at most four devices run concurrently.
                list(executor.map(run, targets))
        except Exception:
            with self.lock:
                self.state["error"] = "Результат команды неизвестен. Проверьте ASIC; команда не повторяется."
        finally:
            with self.lock:
                self.state.update(running=False, cancelled=self.cancel_event.is_set())

    def export_csv(self):
        rows = json.loads(self.snapshot())["rows"]
        output = io.StringIO()
        writer = csv.writer(output, delimiter=";")
        writer.writerow(["IP", "Device ID", "Model", "Firmware", "Firmware version", "Hashrate",
                         "Unit", "Temperature C", "State", "Stale", "Observed at UTC"])
        for row in rows:
            identity, telemetry = row["identity"], row["telemetry"]
            values = [identity["ip"], identity["device_id"], identity["model"], identity["firmware"],
                      identity["firmware_version"], telemetry["rate"], telemetry["rate_unit"],
                      max(telemetry["temperatures_c"], default=None), telemetry["mining_state"],
                      telemetry["stale"], telemetry["observed_at"]]
            writer.writerow([csv_cell(value) for value in values])
        return "\ufeff" + output.getvalue()
