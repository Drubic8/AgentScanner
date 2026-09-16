import ast
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from uuid import uuid4
from threading import Event
import unittest
from unittest.mock import patch

from miner_scanner.commands import execute_command, mode_payload
from miner_scanner.models import Credentials
from miner_scanner.normalization import normalize
from miner_scanner.profiles import ProfileRegistry, signatures
from miner_scanner.ranges import expand_ranges
from miner_scanner.repository import DeviceRepository
from miner_scanner.runtime import Operation, ProtocolError, ScanOptions
from miner_scanner.service import ScannerService
from tests.fakes import FakeFactory, stock


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.factory = FakeFactory()
        self.repository = DeviceRepository(":memory:")
        self.service = ScannerService(repository=self.repository, transport_factory=self.factory)
        self.addCleanup(self.repository.close)

    def test_ranges_validate_deduplicate_and_exclude(self):
        self.assertEqual(expand_ranges(["192.0.2.1-3", "192.0.2.2/31"], exclusions=["192.0.2.2"]), ["192.0.2.1", "192.0.2.3"])
        self.assertEqual(expand_ranges("192.0.2.5/32"), ["192.0.2.5"])
        for value in ["garbage", "192.0.2.5-1", "0.0.0.0/0", "::1", ""]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                expand_ranges(value)

    def test_warm_poll_uses_only_dynamic_queries(self):
        cold = self.service.poll("192.0.2.1")
        cold_count = cold.display["RequestCount"]
        self.factory.calls.clear()
        warm = self.service.poll("192.0.2.1")
        self.assertEqual(warm.identity.device_id, cold.identity.device_id)
        self.assertEqual([key for _, key in self.factory.calls], ["stats", "summary", "config"])
        self.assertLess(warm.display["RequestCount"], cold_count)
        self.assertEqual(warm.telemetry.rate, 200e12)

    def test_unit_conversion_uses_field_not_magnitude(self):
        for value in (0, 1, 499, 10000000):
            result = normalize({"summary": {"SUMMARY": [{"MHS 5s": value}]}}, {"Algo": "Scrypt"}, "antminer")
            self.assertEqual(result.rate, value * 1e6)
        missing = normalize({}, {}, "generic")
        self.assertIsNone(missing.rate)
        self.assertIsNone(missing.uptime_seconds)

    def test_pool_text_cannot_identify_a_firmware(self):
        data = {"summary": {"SUMMARY": [{"Elapsed": 42}]}, "pools": {"POOLS": [{"User": "vnish antminer avalon"}]}}
        self.assertEqual(signatures(data), {"cgminer"})
        self.assertEqual(ProfileRegistry().resolve(data).id, "generic.cgminer")

    def test_conflicting_vendors_are_ambiguous(self):
        data = stock()
        data["stats"]["STATS"][0]["Type"] = "Avalon 1366"
        self.assertIsNone(ProfileRegistry().resolve(data))

    def test_exact_profile_overrides_family_without_registration_order(self):
        family = ProfileRegistry().by_id["bitmain.stock"]
        specific = replace(family, id="test.exact", priority=50, match={"models": ["Antminer S21"], "firmware_versions": ["test-1"]}, verified_commands=("mining_stop",))
        for profiles in ([family, specific], [specific, family]):
            registry = ProfileRegistry(profiles)
            self.assertEqual(registry.resolve(stock()).id, "test.exact")
            changed = stock()
            changed["version"]["VERSION"][0]["firmware_version"] = "test-2"
            self.assertEqual(registry.resolve(changed).id, "bitmain.stock")

    def test_profile_conflict_never_picks_first(self):
        family = ProfileRegistry().by_id["bitmain.stock"]
        registry = ProfileRegistry([family, replace(family, id="duplicate-match")])
        self.assertIsNone(registry.resolve(stock()))

    def test_open_rpc_port_with_non_asic_reply_does_not_hide_antminer(self):
        self.factory.data["rpc_info"] = {"code": 0, "msg": {"service": "printer"}}
        self.assertEqual(self.service.poll("192.0.2.1").identity.make, "Bitmain")

    def test_failed_poll_preserves_values_as_stale(self):
        previous = self.service.poll("192.0.2.1")
        self.factory.data["stats"] = None
        self.factory.data["summary"] = None
        self.factory.data["config"] = None
        result = self.service.poll("192.0.2.1")
        self.assertTrue(result.telemetry.stale)
        self.assertEqual(result.telemetry.rate, previous.telemetry.rate)

    def test_streaming_and_cancel_preserve_completed_results(self):
        cancel = Event()
        streamed = []
        def received(record):
            streamed.append(record)
            cancel.set()
        results = self.service.scan("192.0.2.1-30", cancel=cancel, options=ScanOptions(workers=1), on_result=received)
        self.assertEqual(len(results), 1)
        self.assertEqual(results, streamed)

    def test_invalid_range_sends_no_requests(self):
        with self.assertRaises(ValueError):
            self.service.scan(["192.0.2.1", "oops"])
        self.assertFalse(self.factory.calls)

    def test_cancel_before_start_sends_no_requests(self):
        cancel = Event()
        cancel.set()
        self.assertEqual(self.service.scan("192.0.2.1", cancel=cancel), [])
        self.assertFalse(self.factory.calls)

    def test_repository_restart_marks_snapshot_stale_without_raw_secrets(self):
        record = self.service.poll("192.0.2.1")
        path = Path(__file__).resolve().parent / f".test-{uuid4().hex}.sqlite3"
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        repository = DeviceRepository(path)
        try:
            repository.save(record)
            repository.close()
            repository = DeviceRepository(path)
            restored = repository.get("192.0.2.1")
            repository.close()
            self.assertEqual(restored.identity.device_id, record.identity.device_id)
            self.assertTrue(restored.telemetry.stale)
            self.assertNotIn(b'"pass"', path.read_bytes())
        finally:
            repository.close()

    def test_credential_repr_does_not_disclose_secret(self):
        self.assertNotIn("secret-value", repr(Credentials("root", "secret-value")))

    def test_firmware_change_before_command_sends_no_write(self):
        record = self.service.poll("192.0.2.1")
        self.factory.data["version"]["VERSION"][0]["firmware_version"] = "test-2"
        result = execute_command(self.service, "192.0.2.1", "reboot", device_id=record.identity.device_id, allow_unverified=True)
        self.assertEqual(result.status, "skipped")
        self.assertFalse(self.factory.writes)

    def test_unverified_control_is_disabled_by_default(self):
        self.service.poll("192.0.2.1")
        result = execute_command(self.service, "192.0.2.1", "sleep")
        self.assertEqual(result.status, "unsupported")
        self.assertFalse(self.factory.writes)

    def test_mode_command_preserves_config_and_verifies(self):
        self.service.poll("192.0.2.1")
        self.service.set_credentials("192.0.2.1", Credentials("root", "test"))
        with patch.object(Operation, "pause", lambda *_: None):
            result = execute_command(self.service, "192.0.2.1", "sleep", allow_unverified=True)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(len(self.factory.writes), 1)
        payload = self.factory.writes[0][2]
        self.assertNotIn("miner-mode", payload)
        self.assertEqual(payload["pools"], stock()["config"]["pools"])

    def test_http_200_without_state_change_is_not_success(self):
        self.service.poll("192.0.2.1")
        self.service.set_credentials("192.0.2.1", Credentials("root", "test"))
        self.factory.apply_mode = False
        with patch.object(Operation, "pause", lambda *_: None):
            result = execute_command(self.service, "192.0.2.1", "sleep", allow_unverified=True)
        self.assertEqual(result.status, "unconfirmed")
        self.assertEqual(len(self.factory.writes), 1)

    def test_reboot_timeout_is_unconfirmed_and_is_never_replayed(self):
        self.service.poll("192.0.2.1")
        self.service.set_credentials("192.0.2.1", Credentials("root", "test"))
        self.factory.write_error = TimeoutError()
        first = execute_command(self.service, "192.0.2.1", "reboot", command_id="same-request", allow_unverified=True)
        second = execute_command(self.service, "192.0.2.1", "reboot", command_id="same-request", allow_unverified=True)
        self.assertEqual(first.status, "unconfirmed")
        self.assertEqual(second, first)
        self.assertEqual(len(self.factory.writes), 1)

    def test_unknown_payload_and_masked_configuration_are_rejected(self):
        with self.assertRaises(ProtocolError):
            mode_payload({}, 1, "Antminer S21")
        with self.assertRaises(ProtocolError):
            mode_payload({**stock()["config"], "password": "*****"}, 1, "Antminer S21")
        with self.assertRaises(ProtocolError):
            mode_payload(stock()["config"], 1, "Unknown")

    def test_new_power_mode_needs_only_a_profile_rule(self):
        family = ProfileRegistry().by_id["bitmain.stock"]
        profile = replace(family, id="synthetic.power", priority=50,
                          match={"models": ["Antminer S21"], "firmware_versions": ["test-1"]},
                          verified_commands=("low",), command_rules={"low": {
                              "path": "/cgi-bin/set_miner_conf.cgi", "payload": {"power_profile": "low"},
                              "read_config": "/cgi-bin/get_miner_conf.cgi",
                              "verify": {"path": "/cgi-bin/get_miner_conf.cgi", "field": ["power_profile"], "equals": "low"},
                          }})
        self.service.registry = ProfileRegistry([profile])
        record = self.service.poll("192.0.2.1")
        self.assertEqual(record.capabilities["low"], "supported")
        self.service.set_credentials("192.0.2.1", Credentials("root", "test"))
        with patch.object(Operation, "pause", lambda *_: None):
            result = execute_command(self.service, "192.0.2.1", "low")
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(self.factory.writes[0][2]["pools"], stock()["config"]["pools"])

    def test_explicit_metric_mapping_for_new_firmware(self):
        result = normalize({"vendor_status": {"rate": 200}}, {}, "generic", {"rate": {"path": ["vendor_status", "rate"], "unit": "TH/s"}})
        self.assertEqual(result.rate, 200e12)

    def test_whatsminer_documented_ths_never_changes_with_magnitude(self):
        for value in (0, 101.847, 15000):
            data = {"rpc_summary": {"msg": {"summary": {"hash-realtime": value}}}}
            self.assertEqual(normalize(data, {}, "whatsminer").rate, value * 1e12)

    def test_device_credentials_override_session_discovery_credentials(self):
        default = Credentials("root", "default")
        specific = Credentials("super", "specific")
        self.service.set_default_credentials(default)
        self.service.set_credentials("192.0.2.1", specific)
        self.assertIs(self.service.credentials_for("192.0.2.1"), specific)
        self.assertIs(self.service.credentials_for("192.0.2.2"), default)

    def test_unsupported_mode_sends_no_write_even_in_compatibility_mode(self):
        self.service.poll("192.0.2.1")
        result = execute_command(self.service, "192.0.2.1", "hem", allow_unverified=True)
        self.assertEqual(result.status, "unsupported")
        self.assertFalse(self.factory.writes)

    def test_pure_parsers_never_import_network_or_gui(self):
        directory = Path(__file__).parents[1] / "miner_scanner" / "parsers"
        for path in directory.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imports = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
            self.assertFalse(set(imports) & {"requests", "socket", "PyQt6"}, path.name)


if __name__ == "__main__":
    unittest.main()
