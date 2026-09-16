"""Versioned local passports. Raw API replies and passwords are not persisted."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import sqlite3
from threading import RLock

from .models import DeviceIdentity, DeviceRecord, TelemetrySnapshot


def default_database():
    override = os.environ.get("MINER_SCANNER_DATA_DIR")
    base = Path(override) if override else Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "ASICMonitor"
    return base / "devices.sqlite3"


class DeviceRepository:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None and str(path) != ":memory:" else path
        self.lock = RLock()
        self._connection = None

    def _db(self):
        if self._connection is None:
            path = self.path if self.path is not None else default_database()
            if str(path) != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(str(path), check_same_thread=False)
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                connection.close()
                raise RuntimeError("Unsupported scanner database version")
            connection.execute("CREATE TABLE IF NOT EXISTS devices (ip TEXT PRIMARY KEY, record TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, result TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
            connection.execute("PRAGMA user_version=1")
            connection.commit()
            self._connection = connection
        return self._connection

    def get(self, ip):
        with self.lock:
            row = self._db().execute("SELECT record FROM devices WHERE ip=?", (ip,)).fetchone()
        if not row:
            return None
        data = json.loads(row[0])
        telemetry = TelemetrySnapshot(**data["telemetry"])
        telemetry.stale = True
        return DeviceRecord(DeviceIdentity(**data["identity"]), telemetry, data["display"], data["capabilities"])

    def save(self, record):
        with self.lock:
            connection = self._db()
            with connection:
                connection.execute("INSERT OR REPLACE INTO devices VALUES (?, ?)", (record.identity.ip, json.dumps(asdict(record), ensure_ascii=False)))

    def journal(self, result):
        with self.lock:
            connection = self._db()
            with connection:
                connection.execute("INSERT OR REPLACE INTO commands(id,result) VALUES (?,?)", (result.command_id, json.dumps(asdict(result), ensure_ascii=False)))
                connection.execute("DELETE FROM commands WHERE created_at < datetime('now', '-30 days')")

    def command_result(self, command_id):
        from .models import CommandResult
        with self.lock:
            row = self._db().execute("SELECT result FROM commands WHERE id=?", (command_id,)).fetchone()
        return CommandResult(**json.loads(row[0])) if row else None

    def close(self):
        with self.lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
