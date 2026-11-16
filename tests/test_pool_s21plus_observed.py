"""Observed S21+ read shapes with synthetic settings; offline previews only.

No settings setter is invoked. The live observation supplied field types and
technical identity, not hardware write/readback evidence or setting values.
"""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from miner_scanner import pool_backends as backends, stock_compatibility
from miner_scanner.pool_configuration import PoolEdit, WorkerNaming, prepare_pool_batch
from miner_scanner.pool_evidence import configuration_evidence, sanitize_evidence
from miner_scanner.runtime import Operation
from tests.test_pool_integration import OfflineDevice, OfflineService, OfflineTransport


FIXTURE = Path(__file__).parent / "fixtures" / "pool_s21plus_read_20261109.json"


class DryRunTransport(OfflineTransport):
    def http(self, path, method="GET", **kwargs):
        if method != "GET":
            raise AssertionError("A dry-run test attempted a settings/control POST")
        return super().http(path, method, **kwargs)


class ObservedS21PlusPreviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.stock_ip, self.vnish_ip = "192.0.2.20", "192.0.2.10"
        self.devices = {self.stock_ip: OfflineDevice(), self.vnish_ip: OfflineDevice("VNish")}
        self.service = OfflineService(self.devices)
        self.addCleanup(self.service.repository.close)
        self.service.transport_factory = lambda ip, op, credentials: DryRunTransport(self.devices[ip], ip, op)
        for ip, key in ((self.stock_ip, "stock"), (self.vnish_ip, "vnish")):
            source = self.fixture[key]
            self.devices[ip].config = deepcopy(source["config"])
            for name, value in source["identity"].items():
                setattr(self.service.records[ip].identity, name, value)
        source = Path(__file__).resolve().parents[1] / "docs" / "vnish_openapi.json"
        self.devices[self.vnish_ip].spec = json.loads(source.read_text(encoding="utf-8"))
        self.digest, self.definition = next((digest, definition) for digest, definition
            in stock_compatibility.interfaces()["miner"].items()
            if definition["source_asset"] == self.fixture["stock"]["observed_asset"])
        # The observation identified this exact audited asset. Its bytes and
        # original settings are not copied into the fixture or executed.
        probe = patch.object(stock_compatibility, "probe", return_value={"miner": self.digest})
        probe.start()
        self.addCleanup(probe.stop)

    def prepare(self, edits, ips=None):
        return list(prepare_pool_batch(self.service,
            [self.service.records[ip].to_legacy() for ip in (ips or (self.stock_ip, self.vnish_ip))], edits))

    def assert_no_setters(self):
        for device in self.devices.values():
            self.assertFalse(device.writes)
            self.assertTrue(all(method == "GET" or path == "/api/v1/unlock" for path, method in device.reads))

    def test_provenance_limits_claims_and_stock_shape_matches_observed_types(self):
        metadata = self.fixture["provenance"]
        self.assertIs(metadata["read_structure_observed"], True)
        self.assertIs(metadata["all_configuration_values_are_synthetic"], True)
        self.assertIs(metadata["hardware_write_verified"], False)
        self.assertEqual(metadata["setter_requests_sent_in_observation"], 0)
        config = self.fixture["stock"]["config"]
        expected_types = {"bitmain-user-ip-cat": bool, "bitmain-fan-ctrl": bool,
            "bitmain-fan-pwm": int, "bitmain-freq": int, "bitmain-voltage": float,
            "bitmain-work-mode": int, "bitmain-freq-level": int}
        for key, expected in expected_types.items():
            self.assertIs(type(config[key]), expected, key)
        for slot in config["pools"]:
            self.assertEqual(set(slot), {"url", "user", "pass"})
            self.assertTrue(all(type(value) is str for value in slot.values()))
        safe = sanitize_evidence(configuration_evidence(config, "stock.cgi"))
        self.assertEqual(safe["schema_digest"], self.fixture["stock"]["observed_schema_digest"])
        self.assert_no_setters()

    def test_stock_exact_asset_selects_bitmain_work_mode_map_without_changing_configuration(self):
        device = self.devices[self.stock_ip]
        baseline = deepcopy(device.config)
        with self.service.transport_factory(self.stock_ip, Operation(), self.service.credentials_for(self.stock_ip)) as transport:
            state = backends.read_state(self.service, self.service.records[self.stock_ip], transport)
        self.assertEqual(state.backend_id, "bitmain.stock.cgi")
        self.assertEqual(state.opaque["contract"]["mode_field"], "bitmain-work-mode")
        self.assertEqual(state.opaque["contract"]["mapping"], self.definition["required"])
        self.assertEqual(state.slots, baseline["pools"])
        backends.validate_state(state, state.slots)
        self.assertEqual(device.config, baseline)
        self.assert_no_setters()

    def test_common_worker_prefix_ip_and_index_previews_preserve_all_unedited_values(self):
        for mode in ("index", "ip"):
            with self.subTest(mode=mode):
                baseline = {ip: deepcopy(device.config) for ip, device in self.devices.items()}
                plans = {plan.ip: plan for plan in self.prepare([PoolEdit(0, worker=WorkerNaming("TEST", mode, width=3))])}
                for index, ip in enumerate((self.vnish_ip, self.stock_ip), 1):
                    plan = plans[ip]
                    self.assertEqual(plan.status, "ready", plan.message)
                    generated = f"TEST.{index:03}" if mode == "index" else "TEST." + ip.replace(".", "_")
                    self.assertEqual([(change.field, change.after) for change in plan.changes], [("user", generated)])
                    expected = json.loads(plan._prepared.expected)
                    original_slots = baseline[ip]["pools"] if ip == self.stock_ip else baseline[ip]["miner"]["pools"]
                    self.assertEqual(expected[1:], original_slots[1:])
                    self.assertEqual(expected[0]["url"], original_slots[0]["url"])
                    self.assertEqual(expected[0]["pass"], original_slots[0]["pass"])
                    self.assertEqual(expected[0]["user"], generated)
                    backends.validate_state(plan._prepared.state, expected)
                    # Pure payload construction proves the dry-run preservation
                    # invariant; the transport setter is intentionally unusable.
                    payload = backends._write_payload(plan._prepared.state, expected)
                    if ip == self.stock_ip:
                        self.assertEqual(payload, {**baseline[ip], "pools": expected})
                        self.assertEqual(plan.firmware_version, "Wed Feb 12 18:01:32 CST 2025")
                    else:
                        self.assertEqual(payload, {"miner": {"pools": expected}})
                        self.assertEqual(plan.model, "s21plus")
                        self.assertEqual(plan.firmware_version, "1.3.3")
                    self.assertEqual(self.devices[ip].config, baseline[ip])
                self.assert_no_setters()

    def test_stock_masked_or_missing_untouched_password_refuses_ready(self):
        device = self.devices[self.stock_ip]
        for unknown in ("********", None):
            with self.subTest(unknown_kind="masked" if unknown else "missing"):
                device.config = deepcopy(self.fixture["stock"]["config"])
                if unknown is None:
                    device.config["pools"][2].pop("pass")
                else:
                    device.config["pools"][2]["pass"] = unknown
                plans = self.prepare([PoolEdit(0, worker=WorkerNaming("TEST"))], ips=(self.stock_ip,))
                self.assertEqual(plans[0].status, "unsupported")
                self.assertIsNone(plans[0]._prepared)
                self.assert_no_setters()

    def test_stock_explicit_replacement_of_unknown_password_is_previewed_only(self):
        device = self.devices[self.stock_ip]
        device.config["pools"][2].pop("pass")
        baseline = deepcopy(device.config)
        plan = self.prepare([PoolEdit(0, worker=WorkerNaming("TEST")),
                             PoolEdit(2, password="synthetic-explicit-replacement")], ips=(self.stock_ip,))[0]
        self.assertEqual(plan.status, "ready", plan.message)
        self.assertEqual(json.loads(plan._prepared.expected)[2]["pass"], "synthetic-explicit-replacement")
        self.assertEqual(device.config, baseline)
        self.assert_no_setters()

    def test_stock_firmware_ip_suffix_blocks_worker_change_without_toggling_flag(self):
        device = self.devices[self.stock_ip]
        device.config["bitmain-user-ip-cat"] = True
        baseline = deepcopy(device.config)
        refused = self.prepare([PoolEdit(0, worker=WorkerNaming("TEST"))], ips=(self.stock_ip,))[0]
        self.assertEqual(refused.status, "unsupported")
        self.assertIsNone(refused._prepared)
        url_only = self.prepare([PoolEdit(0, url="stratum+tcp://synthetic-new.example.invalid:3333")], ips=(self.stock_ip,))[0]
        self.assertEqual(url_only.status, "ready", url_only.message)
        self.assertIs(url_only._prepared.state.opaque["config"]["bitmain-user-ip-cat"], True)
        self.assertEqual(device.config, baseline)
        self.assert_no_setters()

    def test_vnish_raw_model_and_full_local_openapi_gate_remain_required(self):
        device = self.devices[self.vnish_ip]
        for missing in ("get", "post"):
            with self.subTest(missing=missing):
                saved = device.spec["paths"]["/settings"].pop(missing)
                try:
                    plan = self.prepare([PoolEdit(0, worker=WorkerNaming("TEST"))], ips=(self.vnish_ip,))[0]
                    self.assertEqual(plan.status, "unsupported")
                    self.assertIsNone(plan._prepared)
                finally:
                    device.spec["paths"]["/settings"][missing] = saved
        self.assertEqual(self.service.records[self.vnish_ip].identity.model, "s21plus")
        self.assertEqual(device.config, self.fixture["vnish"]["config"])
        self.assert_no_setters()


if __name__ == "__main__":
    unittest.main()
