import json
from pathlib import Path
import tempfile
from threading import Event
import unittest
from zipfile import ZipFile

from miner_scanner.diagnostics import Capture, RecordingTransport, collect_diagnostics, redact
from miner_scanner.models import Credentials
from miner_scanner.runtime import Operation, ScanOptions
from tests.fakes import FakeFactory


class DiagnosticTests(unittest.TestCase):
    def test_redaction_retains_model_and_numbers_but_not_secrets(self):
        raw = {"model": "Antminer Z15", "firmware_version": "v1.2.3",
               "temp": 60, "User": "worker.foo", "password": "123456",
               "unknown": "wallet data", "nested": [{"token": "abc", "serial": 12345}],
               "ip": "192.0.2.1", "description": "user=root pass=SECRET",
               "version": "build SECRET", "192.0.2.1": "host"}
        result = redact(raw, secrets=("SECRET",))
        self.assertEqual(result["model"], "Antminer Z15")
        self.assertEqual(result["temp"], 60)
        encoded = json.dumps(result)
        for value in ("123456", "12345", "worker.foo", "192.0.2.1", "SECRET", "wallet data"):
            self.assertNotIn(value, encoded)

    def test_control_and_unknown_reads_rejected_before_transport(self):
        factory = FakeFactory()
        capture = Capture(["192.0.2.1"])
        with RecordingTransport("192.0.2.1", Operation(), None, capture, factory) as transport:
            for call in (lambda: transport.http("/cgi-bin/reboot.cgi"),
                         lambda: transport.http_json("/cgi-bin/get_miner_conf.cgi", "POST"),
                         lambda: transport.cgminer("ascset"),
                         lambda: transport.rpc("set.system.reboot"),
                         lambda: transport.http("/api/v1/info", payload={"x": 1})):
                with self.assertRaises(ValueError):
                    call()
        self.assertEqual(factory.calls, [])

    def test_fake_scan_archive_and_cached_poll_without_writes(self):
        factory = FakeFactory()
        capture, manifest = collect_diagnostics(["192.0.2.1"], samples=2, interval=0,
            options=ScanOptions(workers=1), credentials=Credentials("root", "SECRET"), factory=factory)
        self.assertEqual(manifest["fresh_records"], 2)
        self.assertEqual(factory.writes, [])
        self.assertTrue(any('"kind": "request"' in line for line in capture.events))
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "test.zip"
            capture.save(destination, manifest)
            with ZipFile(destination) as archive:
                text = archive.read("events.jsonl").decode()
                self.assertNotIn("192.0.2.1", text)
                self.assertNotIn("SECRET", text)
                self.assertIn("Antminer S21", text)
                self.assertIn("device-001", text)
            with self.assertRaises(FileExistsError):
                capture.save(destination, manifest)

    def test_response_and_exception_handling(self):
        factory = FakeFactory({"stats": OSError("password SECRET 192.0.2.1")})
        capture = Capture(["192.0.2.1"])
        with RecordingTransport("192.0.2.1", Operation(), None, capture, factory) as transport:
            with self.assertRaises(OSError):
                transport.cgminer("stats")
        self.assertIn("OSError", capture.events[0])
        self.assertNotIn("SECRET", capture.events[0])

    def test_cancel_and_size_limit(self):
        cancel = Event()
        cancel.set()
        factory = FakeFactory()
        capture, manifest = collect_diagnostics(["192.0.2.1"], samples=2, interval=0,
            options=ScanOptions(workers=1), cancel=cancel, factory=factory)
        self.assertTrue(manifest["cancelled"])
        self.assertEqual(factory.calls, [])
        capture.max_bytes = 1
        capture.add("192.0.2.1", "request", response={"x": 1})
        self.assertEqual(capture.dropped, 1)
        self.assertEqual(capture.events, [])
