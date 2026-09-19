"""Android boundary: bounded worker, JSON DTOs, no Android imports or credentials on disk."""
from dataclasses import asdict
import csv
import io
import json
from pathlib import Path
import re
from threading import Event, RLock, Thread

from miner_scanner.commands import execute_command
from miner_scanner.models import Credentials
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


class MobileScanner:
    def __init__(self, data_dir, service=None):
        self.service = service or ScannerService(repository=DeviceRepository(Path(data_dir) / "devices.sqlite3"))
        self.lock = RLock()
        self.cancel_event = Event()
        self.thread = None
        self.state = {"running": False, "cancelled": False, "processed": 0,
                      "total": 0, "errors": 0, "error": "", "rows": [], "command": None}

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
                          "total": len(addresses), "errors": 0, "error": "", "rows": [], "command": None}
            self.thread = Thread(target=self._scan, args=(addresses, options), daemon=True, name="mobile-scan")
            self.thread.start()

    def _scan(self, addresses, options):
        def received(record):
            # Explicit DTO: never serialize raw replies, worker names, pools or credentials.
            row = {"identity": asdict(record.identity), "telemetry": asdict(record.telemetry),
                   "capabilities": dict(record.capabilities)}
            row["telemetry"].pop("diagnostics", None)
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

    def command(self, ip, device_id, action):
        with self.lock:
            if self.state["running"]:
                raise ValueError("An operation is already running")
            # No implicit experimental mode. Re-check the exact identity in the shared executor.
            record = self.service.get_record(ip)
            profile = self.service.registry.by_id.get(record.identity.profile_id) if record else None
            if not record or record.identity.device_id != device_id or not profile or action not in profile.verified_commands:
                raise ValueError("Команда не подтверждена для этой модели и прошивки")
            self.cancel_event = Event()
            self.state.update(running=True, command=None, cancelled=False)
            self.thread = Thread(target=self._command, args=(ip, device_id, action), daemon=True)
            self.thread.start()

    def _command(self, ip, device_id, action):
        try:
            result = execute_command(self.service, ip, action, device_id=device_id, cancel=self.cancel_event)
            with self.lock:
                self.state["command"] = asdict(result)
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
