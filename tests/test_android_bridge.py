"""Mobile contract tests use synthetic transports only; no ASIC or LAN required."""
import importlib.util
import json
from pathlib import Path
from threading import Event
import unittest

from miner_scanner.repository import DeviceRepository
from miner_scanner.service import ScannerService
from tests.fakes import FakeFactory

spec = importlib.util.spec_from_file_location("android_bridge", Path(__file__).resolve().parents[1] /
    "apps/android/app/src/main/python/android_bridge.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class AndroidBridgeTests(unittest.TestCase):
    def setUp(self):
        self.factory = FakeFactory()
        self.repository = DeviceRepository(":memory:")
        self.addCleanup(self.repository.close)
        self.service = ScannerService(repository=self.repository, transport_factory=self.factory)
        self.mobile = bridge.MobileScanner("unused", self.service)

    def finish(self):
        self.mobile.thread.join(5)
        self.assertFalse(self.mobile.thread.is_alive())
        return json.loads(self.mobile.snapshot())

    def test_invalid_range_does_not_start_network_and_large_range_is_bounded(self):
        for ranges in ("192.0.2.1\noops", "10.0.0.0/8", ""):
            with self.assertRaises(ValueError):
                self.mobile.start(ranges)
        self.assertFalse(self.factory.calls)
        self.assertFalse(json.loads(self.mobile.snapshot())["running"])

    def test_scan_streams_normalized_devices_and_credentials_are_not_exported(self):
        self.mobile.start("192.0.2.1, 192.0.2.1;192.0.2.2", "operator", "sensitive-test-password")
        result = self.finish()
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["processed"], 2)
        self.assertEqual(len(result["rows"]), 2)
        self.assertIn("profile_id", result["rows"][0]["identity"])
        self.assertNotIn("diagnostics", result["rows"][0]["telemetry"])
        self.assertNotIn("sensitive-test-password", self.mobile.snapshot() + self.mobile.export_csv())
        self.assertTrue(self.mobile.export_csv().startswith("\ufeffIP;"))

    def test_cancellation_and_overlapping_scans(self):
        started, release = Event(), Event()
        def scan(*_args, **kwargs):
            started.set()
            release.wait(3)
            self.assertTrue(kwargs["cancel"].is_set())
        self.service.scan = scan
        self.mobile.start("192.0.2.1")
        self.assertTrue(started.wait(2))
        try:
            with self.assertRaises(ValueError):
                self.mobile.start("192.0.2.2")
            self.mobile.cancel()
        finally:
            release.set()
        self.assertTrue(self.finish()["cancelled"])

    def test_unverified_command_cannot_write_and_csv_cannot_execute_formulas(self):
        self.mobile.start("192.0.2.1")
        result = self.finish()
        identity = result["rows"][0]["identity"]
        with self.assertRaises(ValueError):
            self.mobile.command(identity["ip"], identity["device_id"], "reboot")
        self.assertFalse(self.factory.writes)
        for value in ("=HYPERLINK(\"x\")", " +SUM(1,2)", "@x", "\tx"):
            self.assertTrue(bridge.csv_cell(value).startswith("'"))
        self.assertEqual(bridge.csv_cell(None), "")

    def test_range_preview_matches_actual_address_deduplication(self):
        result = json.loads(bridge.validate_ranges("192.0.2.1-3\n192.0.2.2/31"))
        self.assertEqual(result, {"count": 3, "error": ""})
