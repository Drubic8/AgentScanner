"""S19k Pro read evidence and synthetic settings joined to the real pool core.

The bounded production probe hashes synthetic asset bytes against a test-only
copy of the reviewed catalog entry. No miner JS is executed, and all transport
operations are emulated. Successful emulated writes are not hardware evidence.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from miner_scanner import pool_backends as backends, stock_compatibility as stock
from miner_scanner.pool_configuration import PoolEdit, WorkerNaming, prepare_pool_batch, execute_pool_batch
from miner_scanner.runtime import Operation
from tests.test_pool_integration import OfflineDevice, OfflineService, OfflineTransport


FIXTURE = Path(__file__).parent / "fixtures" / "pool_s19kpro_read_20261109.json"


class PreviewTransport(OfflineTransport):
    def http(self, path, method="GET", **kwargs):
        if method != "GET":
            raise AssertionError("A pool preview attempted a setter")
        return super().http(path, method, **kwargs)


class ObservedS19kProTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.ip = "192.0.2.19"
        self.device = OfflineDevice()
        self.device.config = deepcopy(self.fixture["config"])
        self.service = OfflineService({self.ip: self.device})
        self.addCleanup(self.service.repository.close)
        self.service.transport_factory = lambda ip, op, credentials: PreviewTransport(self.device, ip, op)
        self.record = self.service.records[self.ip]
        for key, value in self.fixture["identity"].items():
            setattr(self.record.identity, key, value)
        self.catalog = deepcopy(stock.interfaces())
        self.definition = self.catalog["miner"].pop(self.fixture["observed_asset_sha256"])
        self.assertEqual(self.definition["source_asset"], self.fixture["observed_asset"])
        self.assertEqual(self.definition["pages"], [self.fixture["observed_page_sha256"]])
        script = b"/* synthetic S19k Pro miner asset: never executed */"
        page = ('<script src="/js/' + self.fixture["observed_asset"] + '"></script>').encode()
        self.digest = hashlib.sha256(script).hexdigest()
        self.page_digest = hashlib.sha256(page).hexdigest()
        synthetic_definition = deepcopy(self.definition)
        synthetic_definition["pages"] = [self.page_digest]
        self.catalog["miner"][self.digest] = synthetic_definition
        self.device.web = {"/miner.html": page, "/js/" + self.fixture["observed_asset"]: script}
        catalog_patch = patch.object(stock, "interfaces", return_value=self.catalog)
        catalog_patch.start()
        self.addCleanup(catalog_patch.stop)
        pause = patch.object(Operation, "pause", return_value=None)
        pause.start()
        self.addCleanup(pause.stop)
        # Any accidental fallback to an actual HTTP/TCP client must fail locally.
        for target in ("requests.sessions.Session.request", "socket.create_connection"):
            guard = patch(target, side_effect=AssertionError("Actual network use is forbidden"))
            guard.start()
            self.addCleanup(guard.stop)

    def prepare(self, edits=None):
        return list(prepare_pool_batch(self.service, [self.record.to_legacy()], edits or [
            PoolEdit(0, worker=WorkerNaming("TEST", width=3))]))[0]

    def test_fixture_contains_observed_types_and_no_hardware_write_claim(self):
        provenance = self.fixture["provenance"]
        self.assertIs(provenance["read_structure_observed"], True)
        self.assertIs(provenance["all_configuration_values_are_synthetic"], True)
        self.assertIs(provenance["hardware_write_verified"], False)
        self.assertEqual(provenance["setter_requests_sent_in_observation"], 0)
        self.assertIsNone(self.record.identity.api_version)
        self.assertFalse({"ip", "serial", "mac", "device_id"} & self.fixture["identity"].keys())
        config = self.device.config
        bool_fields = {"api-listen", "api-network", "bitmain-fan-ctrl", "bitmain-use-vil"}
        expected = set(self.definition["required"].values()) | set(self.definition["read_only"]) | {
            "bitmain-work-mode"}
        self.assertEqual(set(config), expected)
        self.assertEqual(len(self.definition["read_only"]), 9)
        for key, value in config.items():
            if key != "pools":
                self.assertIs(type(value), bool if key in bool_fields else str, key)
        self.assertEqual(len(config["pools"]), 3)
        for slot in config["pools"]:
            self.assertEqual(set(slot), {"url", "user", "pass"})
            self.assertTrue(all(type(value) is str for value in slot.values()))
        self.assertIs(self.definition["config_fallback"], False)
        self.assertIs(self.definition["preserve_mode_type"], True)
        self.assertEqual(self.definition["modes"], {"S19k Pro": {}})

    def test_real_probe_and_planner_build_exact_five_fields_without_sending(self):
        baseline = deepcopy(self.device.config)
        password = "  synthetic:пароль,1 ***  "
        for naming in ("index", "ip"):
            with self.subTest(naming=naming):
                plan = self.prepare([PoolEdit(0, url="stratum+tcp://synthetic-new.example.invalid:7777",
                    worker=WorkerNaming("TEST", naming, width=3), password=password)])
                self.assertEqual(plan.status, "ready", plan.message)
                state = plan._prepared.state
                self.assertIs(state.opaque["contract"]["preserve_mode_type"], True)
                self.assertEqual(state.opaque["contract"]["mode_field"], "miner-mode")
                expected_slots = json.loads(plan._prepared.expected)
                expected_worker = "TEST.001" if naming == "index" else "TEST.192_0_2_19"
                self.assertEqual(expected_slots[0], {
                    "url": "stratum+tcp://synthetic-new.example.invalid:7777",
                    "user": expected_worker, "pass": password})
                self.assertEqual(expected_slots[1:], baseline["pools"][1:])
                payload = backends._write_payload(state, expected_slots)
                self.assertEqual(payload, {"pools": expected_slots,
                    "bitmain-fan-ctrl": baseline["bitmain-fan-ctrl"],
                    "bitmain-fan-pwm": baseline["bitmain-fan-pwm"],
                    "miner-mode": baseline["bitmain-work-mode"],
                    "freq-level": baseline["bitmain-freq-level"]})
                self.assertIs(type(payload["miner-mode"]), str)
                self.assertEqual(json.loads(json.dumps(payload, ensure_ascii=False)), payload)
                self.assertNotIn(password, repr(plan))
                self.assertEqual(self.device.config, baseline)
                self.assertFalse(self.device.writes)

    def test_emulated_apply_preserves_all_nonpool_values_and_two_untouched_slots(self):
        baseline = deepcopy(self.device.config)
        plan = self.prepare()
        self.assertEqual(plan.status, "ready", plan.message)
        self.assertFalse(self.device.writes)
        self.service.transport_factory = lambda ip, op, credentials: OfflineTransport(self.device, ip, op)
        result = dict(execute_pool_batch(self.service, [plan]))[self.ip]
        self.assertEqual(result.status, "succeeded", result.message)
        self.assertTrue(result.accepted_by_api)
        expected = deepcopy(baseline)
        expected["pools"][0]["user"] = "TEST.001"
        self.assertEqual(self.device.config, expected)
        self.assertEqual(len(self.device.writes), 1)
        path, method, kwargs = self.device.writes[0]
        self.assertEqual((path, method), ("/cgi-bin/set_miner_conf.cgi", "POST"))
        self.assertEqual(set(kwargs["payload"]), {
            "pools", "bitmain-fan-ctrl", "bitmain-fan-pwm", "miner-mode", "freq-level"})
        self.assertEqual(kwargs["payload"]["miner-mode"], "2")
        self.assertIs(type(kwargs["payload"]["miner-mode"]), str)

    def test_wrong_model_asset_page_and_unexpected_schema_are_refused(self):
        baseline = deepcopy(self.device.config)
        web = deepcopy(self.device.web)
        for failure in ("generic_s19", "other_model", "unknown_asset", "changed_page", "unknown_field",
                        "invalid_mode", "invalid_pool"):
            with self.subTest(failure=failure):
                self.device.config = deepcopy(baseline)
                self.device.web = deepcopy(web)
                self.record.identity.model = self.fixture["identity"]["model"]
                if failure in {"generic_s19", "other_model"}:
                    self.record.identity.model = "Antminer S19" if failure == "generic_s19" else "Antminer L9"
                elif failure == "unknown_asset":
                    self.device.web["/js/" + self.fixture["observed_asset"]] = b"/* unknown asset */"
                elif failure == "changed_page":
                    self.device.web["/miner.html"] += b"<!-- changed -->"
                elif failure == "unknown_field":
                    self.device.config["unreviewed-setting"] = "synthetic-value"
                elif failure == "invalid_mode":
                    self.device.config["bitmain-work-mode"] = False
                else:
                    self.device.config["pools"][0]["pass"] = {"unreviewed": "synthetic"}
                plan = self.prepare()
                self.assertEqual(plan.status, "unsupported", plan.message)
                self.assertIsNone(plan._prepared)
                self.assertFalse(self.device.writes)

    def test_exact_pool_contract_never_grants_power_or_mode_actions(self):
        self.record.capabilities.update({action: "supported" for action in stock.MODE_ACTIONS})
        with self.service.transport_factory(self.ip, Operation(), self.service.credentials_for(self.ip)) as transport:
            evidence = stock.probe(transport)
        self.assertEqual((evidence["miner"], evidence["miner_page"]), (self.digest, self.page_digest))
        resolved = stock.resolve(self.record, self.device.config, evidence)
        self.assertFalse(stock.MODE_ACTIONS & resolved["rules"].keys())
        self.assertTrue(stock.MODE_ACTIONS <= resolved["blocked"])
        self.assertFalse(self.device.writes)

    def test_preserve_mode_type_is_opt_in_and_other_contracts_still_submit_numeric_mode(self):
        plan = self.prepare()
        state = plan._prepared.state
        slots = json.loads(plan._prepared.expected)
        self.assertEqual(backends._write_payload(state, slots)["miner-mode"], "2")
        state.opaque["contract"].pop("preserve_mode_type")
        numeric = backends._write_payload(state, slots)["miner-mode"]
        self.assertEqual(numeric, 2)
        self.assertIs(type(numeric), int)
        self.assertFalse(self.device.writes)


if __name__ == "__main__":
    unittest.main()
