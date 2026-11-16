"""Every catalog profile has an audited pool route or an I/O-free refusal.

Candidate routing is deliberately weaker evidence than device compatibility:
these tests do not authorize settings writes on matching firmware names.
"""
from dataclasses import replace
from pathlib import Path
import re
import unittest

from miner_scanner.models import DeviceIdentity, DeviceRecord, TelemetrySnapshot
from miner_scanner.pool_backends import PoolBackendError, read_state, unsupported_reason
from miner_scanner.profiles import ProfileRegistry


class NoDeviceAccess:
    def __getattr__(self, name):
        raise AssertionError(f"An unsupported pool interface attempted I/O: {name}")


def catalog_record(profile):
    match = profile.match or {}
    return DeviceRecord(DeviceIdentity(
        device_id="synthetic-" + profile.id,
        ip="192.0.2.10",
        make=profile.make,
        model=match.get("models", ["synthetic model"])[0],
        firmware=profile.firmware,
        firmware_version=match.get("firmware_versions", match.get("compile_times", [None]))[0],
        api_version=match.get("api_versions", [None])[0],
        serial=None,
        mac=None,
        profile_id=profile.id,
        profile_version=profile.version,
        fingerprint="synthetic-fingerprint",
    ), TelemetrySnapshot())


class PoolProfileCoverageTests(unittest.TestCase):
    def test_every_catalog_profile_and_rule_version_is_present_in_audit(self):
        document = (Path(__file__).resolve().parents[1] / "docs" / "development" /
                    "pool_configuration.md").read_text(encoding="utf-8")
        rows = re.findall(r"^\| `([^`]+)` \| (\d+) \|", document, re.MULTILINE)
        self.assertEqual(len(rows), len({identifier for identifier, _ in rows}),
                         "Duplicate profile rows would hide an omitted profile")
        actual = {identifier: int(version) for identifier, version in rows}
        expected = {profile.id: profile.version for profile in ProfileRegistry().profiles}
        self.assertEqual(actual, expected,
                         "New or changed profiles need an explicit pool compatibility audit")

    def test_every_catalog_profile_has_candidate_route_or_refuses_before_io(self):
        for profile in ProfileRegistry().profiles:
            with self.subTest(profile=profile.id):
                record = catalog_record(profile)
                reason = unsupported_reason(record)
                candidate = (profile.id.startswith("bitmain.stock")
                             or profile.id in {"bitmain.vnish", "microbt.rpc3"})
                if candidate:
                    self.assertIsNone(reason)
                else:
                    self.assertIsInstance(reason, str)
                    self.assertTrue(reason)
                    with self.assertRaises(PoolBackendError) as failure:
                        read_state(NoDeviceAccess(), record, NoDeviceAccess())
                    self.assertEqual(failure.exception.status, "unsupported")

    def test_unrecognized_firmware_does_not_inherit_stock_write_route(self):
        record = catalog_record(ProfileRegistry().by_id["bitmain.stock"])
        record.identity = replace(record.identity, firmware="unknown-third-party-firmware")
        self.assertTrue(unsupported_reason(record))
        with self.assertRaises(PoolBackendError) as failure:
            read_state(NoDeviceAccess(), record, NoDeviceAccess())
        self.assertEqual(failure.exception.status, "unsupported")

    def test_vendor_firmware_and_profile_must_agree_before_routing(self):
        registry = ProfileRegistry()
        for profile_id, updates in (
                ("bitmain.stock", {"profile_id": "generic.cgminer"}),
                ("bitmain.stock", {"profile_id": "bitmain.stock-unreviewed"}),
                ("bitmain.stock", {"make": "Unknown"}),
                ("bitmain.vnish", {"make": "Canaan"}),
                ("bitmain.vnish", {"profile_id": "generic.cgminer"}),
                ("microbt.rpc3", {"firmware": "unknown-third-party-firmware"}),
                ("microbt.rpc3", {"profile_id": "generic.cgminer"}),
                ("microbt.rpc3", {"make": "Unknown"}),
                ("generic.cgminer", {"firmware": "VNish"})):
            with self.subTest(profile=profile_id, updates=updates):
                record = catalog_record(registry.by_id[profile_id])
                record.identity = replace(record.identity, **updates)
                self.assertTrue(unsupported_reason(record))
                with self.assertRaises(PoolBackendError) as failure:
                    read_state(NoDeviceAccess(), record, NoDeviceAccess())
                self.assertEqual(failure.exception.status, "unsupported")

    def test_pitbit_exact_mode_profile_still_requires_pool_write_evidence(self):
        profile = ProfileRegistry().by_id["bitmain.pitbit.s21.c074.20240202"]
        self.assertIn("mining_start", profile.verified_commands)
        record = catalog_record(profile)
        self.assertTrue(unsupported_reason(record))
        with self.assertRaises(PoolBackendError):
            read_state(NoDeviceAccess(), record, NoDeviceAccess())

    def test_known_rpc_minor_versions_do_not_replace_fresh_protocol_validation(self):
        profile = ProfileRegistry().by_id["microbt.rpc3"]
        class UnconfirmedRpc:
            def __init__(self):
                self.calls = []

            def rpc(self, command, parameter=None):
                self.calls.append((command, parameter))
                return None

        for version in ("3.0.0", "3.0.3", "3.0.5", "3.0.99", "2.0.5"):
            with self.subTest(api=version):
                record = catalog_record(profile)
                record.identity = replace(record.identity, api_version=version)
                transport = UnconfirmedRpc()
                with self.assertRaises(PoolBackendError) as failure:
                    read_state(NoDeviceAccess(), record, transport)
                self.assertEqual(failure.exception.status, "unsupported")
                self.assertEqual(transport.calls, [("get.device.info", None)])


if __name__ == "__main__":
    unittest.main()
