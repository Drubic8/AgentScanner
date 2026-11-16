"""M50 RPC3 observed read shape, synthetic values, no ASIC/control requests."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from miner_scanner.drivers import make_record
from miner_scanner.pool_backends import PoolBackendError, read_state
from miner_scanner.pool_configuration import PoolEdit, WorkerNaming, prepare_pool_batch, collect_pool_evidence_batch
from miner_scanner.pool_evidence import evidence_report
from miner_scanner.pool_evidence_readers import read_evidence
from miner_scanner.pools import pool_columns
from miner_scanner.profiles import ProfileRegistry
from tests.test_pool_evidence_readers import ReadTransport
from tests.test_pool_integration import OfflineDevice, OfflineService


FIXTURE = Path(__file__).parent / "fixtures" / "whatsminer_m50_rpc3_pools_read.json"


class ObservedWhatsminerPoolTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.registry = ProfileRegistry()
        self.ip = "192.0.2.50"
        data = {key: deepcopy(self.fixture[key]) for key in ("rpc_info", "rpc_pools", "rpc_summary")}
        self.record = make_record(self.registry.by_id["microbt.rpc3"], self.ip, data)
        self.transport = ReadTransport()
        self.transport.data[("rpc", "get.device.info", None)] = deepcopy(data["rpc_info"])
        self.transport.data[("rpc", "get.miner.status", "pools")] = deepcopy(data["rpc_pools"])
        self.transport.data[("rpc", "get.miner.setting", None)] = {
            "code": 0, "desc": "get.miner.setting", "msg": {"btrom-worker-suffix": ""}}
        self.service = OfflineService({self.ip: OfflineDevice()})
        self.addCleanup(self.service.repository.close)
        self.service.registry = self.registry
        self.service.records[self.ip] = self.record
        self.service.transport_factory = lambda *args: self.transport

    def test_observed_metadata_and_absent_passwords_are_explicit_fixture_boundaries(self):
        provenance = self.fixture["provenance"]
        self.assertIs(provenance["hardware_read_verified"], True)
        self.assertIs(provenance["hardware_write_verified"], False)
        self.assertIs(provenance["all_setting_values_are_synthetic"], True)
        expected = self.fixture["identity"]
        for key, value in expected.items():
            self.assertEqual(getattr(self.record.identity, key), value, key)
        entries = self.fixture["rpc_pools"]["msg"]["pools"]
        self.assertEqual([entry["id"] for entry in entries], [1, 2, 3])
        for entry in entries:
            self.assertEqual(set(entry), {"id", "url", "status", "account", "stratum-active", "reject-percent", "last-share-time"})
            self.assertIs(type(entry["id"]), int)
            for key in ("url", "status", "account"):
                self.assertIs(type(entry[key]), str)
            self.assertTrue(all(key not in entry for key in ("pass", "passwd", "password")))

    def test_current_reader_returns_runtime_shape_with_all_passwords_missing(self):
        # The read path needs no API credentials or encryption library.
        service = SimpleNamespace(registry=self.registry)
        result = read_evidence(service, self.record, self.transport)
        self.assertEqual(result["read_source"], "whatsminer.rpc_pools")
        self.assertEqual(result["reading_kind"], "runtime")
        self.assertEqual(len(result["pool_slots"]), 3)
        self.assertTrue(all(slot["fields"] == {"url": "present", "user": "present", "pass": "missing"}
                            for slot in result["pool_slots"]))
        self.assertEqual([request for request, _ in self.transport.calls], [
            ("rpc", "get.device.info", None), ("rpc", "get.miner.status", "pools")])
        report = evidence_report([result])
        self.assertIs(report["hardware_verified"], False)
        serialized = json.dumps(report)
        self.assertNotIn("synthetic-rpc3-salt", serialized)
        self.assertNotIn("SYNTHETIC.pool", serialized)
        self.assertNotIn("synthetic-primary.example.invalid", serialized)

    def test_existing_column_projection_keeps_all_three_ordered_runtime_entries(self):
        original = deepcopy(self.fixture["rpc_pools"])
        columns = pool_columns({"rpc_pools": original}, "whatsminer")
        entries = original["msg"]["pools"]
        for index, entry in enumerate(entries):
            suffix = "" if index == 0 else str(index + 1)
            self.assertEqual(columns["Pool" + suffix], entry["url"])
            self.assertEqual(columns["Worker" + suffix], entry["account"])
            self.assertEqual(self.record.display["Worker" + suffix], entry["account"])
        self.assertEqual(original, self.fixture["rpc_pools"])
        self.assertFalse(any(key.lower() in {"pass", "passwd", "password"} for key in columns))

    def test_runtime_passwords_must_be_explicit_before_preparing_a_write(self):
        state = read_state(self.service, self.record, self.transport)
        self.assertEqual(state.backend_id, "whatsminer.rpc3.pools")
        self.assertTrue(all("pass" not in slot for slot in state.slots))
        self.assertEqual(state.opaque["verification_kind"], "runtime_without_passwords")
        self.transport.calls.clear()
        rows = [self.record.to_legacy()]
        plans = list(prepare_pool_batch(self.service, rows, [PoolEdit(0, worker=WorkerNaming("TEST"))]))
        self.assertEqual(plans[0].status, "unsupported")
        self.assertIn("задайте пароль явно", plans[0].message)
        self.assertIsNone(plans[0]._prepared)
        self.assertEqual([request for request, _ in self.transport.calls], [
            ("rpc", "get.device.info", None), ("rpc", "get.miner.status", "pools"),
            ("rpc", "get.miner.setting", None)])
        self.assertIsNone(self.service.repository.command_result(plans[0].command_id))

    def test_separate_read_only_collection_remains_available_while_writer_is_blocked(self):
        plans = list(collect_pool_evidence_batch(self.service, [self.record.to_legacy()]))
        self.assertEqual(plans[0].status, "observed", plans[0].message)
        self.assertEqual(plans[0].evidence["reading_kind"], "runtime")
        self.assertIsNone(plans[0]._prepared)
        self.assertEqual(plans[0].changes, ())
        self.assertIsNone(self.service.repository.command_result(plans[0].command_id))
        self.assertEqual([request for request, _ in self.transport.calls], [
            ("rpc", "get.device.info", None), ("rpc", "get.miner.status", "pools")])


if __name__ == "__main__":
    unittest.main()
