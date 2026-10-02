"""Mobile contract tests use synthetic transports only; no ASIC or LAN required."""
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from copy import deepcopy
from unittest.mock import patch
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

    def test_mobile_display_uses_algorithm_units_and_hides_stale_metrics(self):
        record = self.service.poll("192.0.2.1")
        record.telemetry.rate, record.telemetry.rate_unit, record.telemetry.algorithm = 16000000000, "H/s", "Scrypt"
        self.assertEqual(bridge.device_row(record)["rate_display"], "16.00 GH/s")
        record.telemetry.rate, record.telemetry.rate_unit, record.telemetry.algorithm = 140000, "Sol/s", "Equihash"
        self.assertEqual(bridge.device_row(record)["rate_display"], "140.00 kSol/s")
        record.telemetry.stale = True
        row = bridge.device_row(record)
        self.assertIsNone(row["telemetry"]["rate"])
        self.assertEqual(row["rate_display"], "—")
        self.assertEqual(row["telemetry"]["temperatures_c"], [])

    def test_new_mobile_service_has_bounded_standard_access_profiles(self):
        with TemporaryDirectory() as directory:
            mobile = bridge.MobileScanner(directory)
            try:
                antminer = mobile.service.credential_candidates("192.0.2.1", "antminer")
                vnish = mobile.service.credential_candidates("192.0.2.1", "vnish")
                whatsminer = mobile.service.credential_candidates("192.0.2.1", "whatsminer")
                self.assertEqual([(item.username, item.password) for item in antminer], [("root", "root")])
                self.assertEqual([item.password for item in vnish], ["admin", "root"])
                self.assertEqual([(item.username, item.password) for item in whatsminer], [("super", "super")])
                self.assertNotIn('"password"', mobile.snapshot())
            finally:
                mobile.service.repository.close()


class AndroidControlTests(unittest.TestCase):
    def setUp(self):
        # Exercise production API-contract discovery, not a mocked capability grant.
        from tests.test_stock_compatibility import StockCompatibilityTests
        fixtures = StockCompatibilityTests()
        fixtures.setUp()
        self.addCleanup(fixtures.doCleanups)
        self.service, self.factory, self.record = fixtures.service()
        self.mobile = bridge.MobileScanner("unused", self.service)
        self.mobile.state["rows"] = [bridge.device_row(self.record)]

    def finish(self):
        self.mobile.thread.join(5)
        self.assertFalse(self.mobile.thread.is_alive())
        return json.loads(self.mobile.snapshot())

    def test_compatible_unknown_build_can_sleep_wake_and_change_mode(self):
        profile = self.service.registry.by_id[self.record.identity.profile_id]
        self.assertFalse(profile.verified_commands)
        self.assertEqual(self.record.capabilities["mining_start"], "supported")
        for action, target in (("normal", "0"), ("sleep", "1"), ("hem", "2"), ("wakeup", "0")):
            with self.subTest(action=action):
                self.mobile.command(self.record.identity.ip, self.record.identity.device_id, action)
                result = self.finish()
                self.assertEqual(result["command"]["status"], "succeeded")
                self.assertEqual(self.factory.data["config"]["bitmain-work-mode"], target)
                self.assertEqual(len(result["commands"]), 1)
                self.assertEqual(result["command_total"], 1)
                self.assertEqual(result["rows"][0]["telemetry"]["mining_state"],
                                 "stopped" if target == "1" else "unknown")
        self.assertEqual(len(self.factory.writes), 4)

    def test_session_credentials_are_applied_to_commands_without_export(self):
        self.mobile.command(self.record.identity.ip, self.record.identity.device_id,
                            "mining_start", "root", "session-only-password")
        self.finish()
        self.assertEqual(self.service._default_credentials.password, "session-only-password")
        self.assertNotIn("session-only-password", self.mobile.snapshot() + self.mobile.export_csv())

    def test_stale_identity_and_unsupported_mode_are_rejected_before_writes(self):
        ip, identity = self.record.identity.ip, self.record.identity.device_id
        with self.assertRaises(ValueError):
            self.mobile.command(ip, "old-identity", "mining_start")
        with self.assertRaises(ValueError):
            self.mobile.command(ip, identity, "low")
        self.record.telemetry.stale = True
        with self.assertRaises(ValueError):
            self.mobile.command(ip, identity, "mining_start")
        self.assertFalse(self.factory.writes)

    def test_batch_reports_unsupported_and_valid_targets_individually(self):
        other = deepcopy(self.record)
        other.identity.ip, other.identity.device_id = "192.0.2.2", "unsupported-device"
        other.capabilities = {}
        self.service._records[other.identity.ip] = other
        self.mobile.state["rows"].append(bridge.device_row(other))
        targets = [{"ip": record.identity.ip, "device_id": record.identity.device_id} for record in (other, self.record)]
        self.mobile.command_many(json.dumps(targets), "mining_start")
        result = self.finish()
        self.assertEqual(result["command_total"], 2)
        self.assertEqual({row["ip"]: row["status"] for row in result["commands"]},
                         {"192.0.2.1": "succeeded", "192.0.2.2": "unsupported"})
        self.assertEqual(len(self.factory.writes), 1)
        self.assertFalse(any(ip == "192.0.2.2" for ip, _ in self.factory.calls))

    def test_batch_validation_does_not_start_network(self):
        target = {"ip": self.record.identity.ip, "device_id": self.record.identity.device_id}
        for targets in ([], [target, target], [dict(target, ip="192.0.2.0/24")],
                        [dict(target, device_id="old-identity")], [dict(target, password="secret")]):
            with self.assertRaises(ValueError):
                self.mobile.command_many(json.dumps(targets), "mining_start")
        self.assertFalse(self.factory.writes)

    def test_cancelled_batch_never_sends_queued_targets(self):
        records = [deepcopy(self.record) for _ in range(6)]
        for index, record in enumerate(records, 1):
            record.identity.ip = f"192.0.2.{index}"
            self.service._records[record.identity.ip] = record
        self.mobile.state["rows"] = [bridge.device_row(record) for record in records]
        started, release = Event(), Event()
        called = []
        from miner_scanner.models import CommandResult
        def execute(_service, ip, _action, **kwargs):
            called.append(ip)
            started.set()
            release.wait(3)
            return CommandResult(ip, "unconfirmed", "Cancelled after dispatch")
        with patch.object(bridge, "execute_command", side_effect=execute):
            self.mobile.command_many(json.dumps([{"ip": record.identity.ip, "device_id": record.identity.device_id}
                                                 for record in records]), "mining_start")
            self.assertTrue(started.wait(2))
            self.mobile.cancel()
            release.set()
            result = self.finish()
        self.assertTrue(result["cancelled"])
        self.assertLessEqual(len(called), 4)
        self.assertEqual(len(result["commands"]), 6)
        self.assertTrue(any(row["status"] == "cancelled" for row in result["commands"]))

    def test_failed_readback_does_not_resend_or_lose_confirmed_outcome(self):
        from miner_scanner.models import CommandResult
        with patch.object(bridge, "execute_command", return_value=CommandResult("once", "succeeded", "Confirmed", True)) as execute:
            with patch.object(self.service, "poll", side_effect=OSError("readback unavailable")):
                self.mobile.command(self.record.identity.ip, self.record.identity.device_id, "mining_start")
                result = self.finish()
        execute.assert_called_once()
        self.assertEqual(result["command"]["status"], "succeeded")
