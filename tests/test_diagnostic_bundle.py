"""Offline support export contracts: useful evidence without known credentials."""
from copy import deepcopy
from dataclasses import dataclass
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from miner_scanner.diagnostic_bundle import (
    MAX_LOG_BYTES, MAX_PACKAGE_BYTES, build_bundle, bundle_preview, write_bundle,
)


@dataclass(frozen=True)
class Log:
    status: str = "succeeded"
    text: str = "chain 2: ASIC count mismatch; expected 120, detected 119\n09:45:01 fan 1: 4560 rpm"
    source: str = "/api/v1/logs/miner"
    collected_at: str = "2026-10-31T12:05:00+00:00"
    kind: str = "miner"
    truncated: bool = False


def device():
    return {
        "IP": "192.0.2.23", "Model": "Antminer S21", "Make": "Bitmain", "Firmware": "VNish",
        "FirmwareVersion": "1.2.7", "ProfileId": "bitmain.vnish", "DeviceId": "private-device-id",
        "Identity": {"serial": "SERIAL-PRIVATE-42", "mac": "aa:bb:cc:dd:ee:ff", "fingerprint": "fingerprint-private",
                     "identified_at": "2026-10-31T12:00:00Z"},
        "Pool": "stratum+tcp://pool.example.net:3333", "Worker": "alice.rig01",
        "Pool2": "pool2.example.net:3333", "Worker2": "alice.rig02", "Pool3": "", "Worker3": "",
        "Status": "Running", "Error": "ASIC count mismatch (B2)", "Real": "199.0 TH/s",
        "Telemetry": {"observed_at": "2026-10-31T12:01:00+00:00", "rate": 199.0,
                      "average_rate": 198.2, "rate_unit": "TH/s", "algorithm": "SHA-256",
                      "uptime_seconds": 87500, "temperatures_c": [67, 73], "fan_rpm": [4560, 4500],
                      "stale": False, "identify_enabled": False, "mining_state": "running"},
        "Capabilities": {"sleep": "supported", "reboot": "supported", "anything-private": "secret"},
        "Credentials": {"password": "NEVER-EXPORT-ME"}, "unrecognized": "private customer data",
    }


class DiagnosticBundleTests(unittest.TestCase):
    def test_keeps_evidence_and_masks_known_identifiers_without_mutation(self):
        row = device()
        original = deepcopy(row)
        log = Log(text=Log.text + "\nLinux 4.9.38-tag- firmware 1.2.6\npeer 192.0.2.23 alice.rig02 SERIAL-PRIVATE-42\nprivate-device-id fingerprint-private\nshared-secret")
        result = build_bundle(row, log, app_version="2.5.0", secrets=("shared-secret",))
        preview = bundle_preview(result)
        for forbidden in (row["IP"], row["Worker2"], "SERIAL-PRIVATE-42", "private-device-id", "fingerprint-private",
                          "shared-secret", "NEVER-EXPORT-ME", "private customer data", "anything-private"):
            self.assertNotIn(forbidden, preview)
        self.assertIn("ASIC count mismatch", preview)
        self.assertIn("expected 120, detected 119", preview)
        self.assertIn("09:45:01 fan 1: 4560 rpm", preview)
        self.assertIn("Linux 4.9.38-tag- firmware 1.2.6", preview)
        self.assertEqual(result["snapshot"]["telemetry"]["rate"], 199.0)
        self.assertEqual(result["snapshot"]["telemetry"]["temperatures_c"], [67, 73])
        self.assertEqual(result["snapshot"]["identity"]["model"], "Antminer S21")
        self.assertEqual(result["snapshot"]["identity"]["firmware_version"], "1.2.7")
        self.assertEqual(result["snapshot"]["pools"]["Worker"], "[redacted]")
        self.assertIsNone(result["snapshot"]["pools"]["Worker3"])
        self.assertEqual(row, original)
        self.assertTrue(result["manifest"]["review_required"])
        self.assertFalse(result["manifest"]["network_upload_performed"])

    def test_unknown_log_credentials_urls_addresses_headers_and_wallets(self):
        text = '''Authorization: Bearer BearerSecretValue
Cookie: session=CookieSecretValue; remember=true
{"password": "json password secret", "board": 2}
password=two words secret
api_key: AnotherSecretValue
user=other-worker.private
wallet=some_wallet_address
serial_number=OTHER-DEVICE-SERIAL
stratum+tcp://username:URLpassword@pool.example.org:3333/?token=QuerySecret
https://example.net/private-report
unknown.example.com:3333
other@example.org
203.0.113.5 aa-bb-cc-dd-ee-11 2001:db8::18
0x1234567890123456789012345678901234567890
bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh
C:\\Users\\PrivateName\\Desktop\\miner.log
/home/private_owner/miner.log
chain 2: 119 chips detected
'''
        preview = bundle_preview(build_bundle({}, Log(text=text)))
        for value in ("BearerSecretValue", "CookieSecretValue", "json password secret", "two words secret",
                      "AnotherSecretValue", "other-worker.private", "some_wallet_address", "OTHER-DEVICE-SERIAL",
                      "URLpassword", "QuerySecret", "pool.example.org", "example.net", "unknown.example.com",
                      "other@example.org", "203.0.113.5", "aa-bb-cc-dd-ee-11", "2001:db8::18",
                      "0x1234567890123456789012345678901234567890", "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh",
                      "PrivateName", "private_owner"):
            self.assertNotIn(value, preview, value)
        self.assertIn("chain 2: 119 chips detected", preview)

    def test_missing_values_are_not_zero_and_stale_snapshot_remains_labelled(self):
        row = device()
        row["Stale"] = True
        row["Telemetry"].update(rate=None, average_rate=float("nan"), uptime_seconds=10**500,
                                temperatures_c=[True, None, float("inf"), 70], fan_rpm=None)
        bundle = build_bundle(row)
        telemetry = bundle["snapshot"]["telemetry"]
        self.assertTrue(telemetry["stale"])
        self.assertIsNone(telemetry["rate"])
        self.assertIsNone(telemetry["average_rate"])
        self.assertIsNone(telemetry["uptime_seconds"])
        self.assertEqual(telemetry["temperatures_c"], [None, None, None, 70])
        self.assertEqual(telemetry["fan_rpm"], [])
        self.assertIsNone(bundle["log"])
        json.dumps(bundle, allow_nan=False)

    def test_quoted_json_and_nested_escaped_auth_headers_are_redacted(self):
        text = '\n'.join((
            '{"authorization":"Bearer PRIVATE_TOKEN"}',
            '{"cookie":"session=PRIVATE_COOKIE"}',
            '{"headers":{"Proxy-Authorization":"Basic PRIVATE_PROXY"}}',
            "{'Set-Cookie': 'session=PRIVATE_SET_COOKIE; HttpOnly'}",
            '{"Authorization":\n "Bearer PRIVATE_MULTILINE"\n}',
            json.dumps({"message": json.dumps({"authorization": "Bearer PRIVATE_ESCAPED_TOKEN"})}),
            json.dumps({"message": json.dumps({"cookie": "session=PRIVATE_ESCAPED_COOKIE"})}),
            json.dumps({"message": json.dumps({"password": "PRIVATE_ESCAPED_PASSWORD"})}),
            json.dumps({"message": json.dumps({"nested": json.dumps({"Proxy-Authorization": "Basic PRIVATE_DOUBLE_ESCAPED"})})}),
            'chain 1: missing chips; expected 120, detected 119',
            '  chain 2: 120 chips detected',
        ))
        bundle = build_bundle({}, Log(text=text))
        preview = bundle_preview(bundle)
        self.assertNotIn("PRIVATE_", preview)
        self.assertIn('chain 1: missing chips; expected 120, detected 119', preview)
        self.assertIn('  chain 2: 120 chips detected', preview)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'support.zip'
            write_bundle(path, bundle)
            with ZipFile(path) as archive:
                self.assertNotIn(b'PRIVATE_', archive.read('device.log'))

    def test_export_records_failed_attempt_without_inventing_log_content(self):
        result = build_bundle(None, {"status": "auth_required", "text": "", "kind": "system"})
        self.assertEqual(result["log"]["status"], "auth_required")
        self.assertEqual(result["log"]["text"], "")
        self.assertIsNone(result["log"]["collected_at"])
        self.assertIsNone(result["snapshot"]["identity"]["model"])

    def test_source_is_basename_and_never_exports_absolute_path_or_query(self):
        result = build_bundle(device(), Log(source="C:\\Users\\PrivateName\\Desktop\\rig.log?token=private-query"))
        self.assertEqual(result["log"]["source"], "rig.log")
        self.assertNotIn("PrivateName", bundle_preview(result))
        self.assertNotIn("private-query", bundle_preview(result))

    def test_text_is_bounded_and_truncation_explicit_for_unicode(self):
        result = build_bundle({}, Log(text="ошибка\n" * (MAX_LOG_BYTES // 6)))
        self.assertTrue(result["log"]["truncated"])
        self.assertLessEqual(len(result["log"]["text"].encode("utf-8")), MAX_LOG_BYTES)
        self.assertIn("ошибка", result["log"]["text"])
        self.assertLess(len(bundle_preview(result).encode("utf-8")), MAX_PACKAGE_BYTES)

    def test_untrusted_control_sequences_removed_text_remains_plain(self):
        result = build_bundle({}, Log(text="\x00\x1b[31merror\u202ereboot now\n<script>do not execute</script>"))
        text = result["log"]["text"]
        self.assertNotIn("\x00", text)
        self.assertNotIn("\x1b", text)
        self.assertNotIn("[31m", text)
        self.assertNotIn("\u202e", text)
        self.assertIn("<script>do not execute</script>", text)

    def test_zip_matches_preview_and_never_overwrites(self):
        bundle = build_bundle(device(), Log())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "support.zip"
            write_bundle(path, bundle)
            before = path.read_bytes()
            with ZipFile(path) as archive:
                self.assertEqual(set(archive.namelist()), {"manifest.json", "device.json", "device.log"})
                for name in archive.namelist():
                    self.assertIn(archive.read(name).decode("utf-8"), bundle_preview(bundle))
                self.assertEqual(json.loads(archive.read("manifest.json"))["log"]["source"], "miner")
            with self.assertRaises(FileExistsError):
                write_bundle(path, bundle)
            self.assertEqual(path.read_bytes(), before)

    def test_oversize_or_invalid_payload_rejected_before_file_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "support.zip"
            huge = build_bundle({}, Log())
            huge["log"]["text"] = "x" * (MAX_LOG_BYTES + 1)
            for bundle in (huge, {"manifest": {}, "snapshot": {}, "log": {"text": 1}}, {"unexpected": "value"}):
                with self.assertRaises(ValueError):
                    write_bundle(path, bundle)
                self.assertFalse(path.exists())

    def test_failed_write_cleans_only_new_file(self):
        class FailingOutput(io.BytesIO):
            def write(self, value):
                raise OSError("disk full")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "support.zip"
            original_open = Path.open
            def open_file(target, mode="r", *args, **kwargs):
                if mode == "xb":
                    with original_open(target, mode):
                        pass
                    return FailingOutput()
                return original_open(target, mode, *args, **kwargs)
            with patch.object(Path, "open", open_file):
                with self.assertRaises(OSError):
                    write_bundle(path, build_bundle({}))
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
