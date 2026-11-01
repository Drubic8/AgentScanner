"""Configured pool/worker slots from synthetic replies; no device/network I/O."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from miner_scanner.drivers import make_record
from miner_scanner.pools import POOL_FIELDS, pool_columns
from miner_scanner.profiles import ProfileRegistry
from miner_scanner.repository import DeviceRepository
from miner_scanner.service import ScannerService
from tests.fakes import FakeFactory, stock


def configured(number):
    return {"url": f"stratum+tcp://pool{number}.example.invalid:3333",
            "user": f"owner.worker-{number}", "pass": f"do-not-store-{number}"}


def vnish_pool(index, **overrides):
    return {"id": index, "url": f"stratum+tcp://pool{index + 1}.example.invalid:3333",
            "user": f"owner.worker-{index + 1}", "pool_type": "UserPool", **overrides}


class PoolSlotTests(unittest.TestCase):
    def test_array_positions_and_blank_middle_slot_are_preserved(self):
        values = pool_columns({"config": {"pools": [configured(1), {}, configured(3)]}}, "antminer")
        self.assertEqual(values["Pool"], configured(1)["url"])
        self.assertEqual(values["Worker"], "owner.worker-1")
        self.assertEqual((values["Pool2"], values["Worker2"]), ("", ""))
        self.assertEqual(values["Worker3"], "owner.worker-3")

    def test_explicit_cgminer_indexes_win_over_response_order(self):
        data = {"pools": {"POOLS": [
            {"POOL": 2, "URL": "third.invalid", "User": "third", "Status": "Alive"},
            {"POOL": "0", "URL": "first.invalid", "User": "first", "Status": "Dead"},
        ]}}
        values = pool_columns(data, "avalon")
        self.assertEqual([values[k] for k in ("Worker", "Worker2", "Worker3")], ["first", "", "third"])

    def test_live_config_wins_over_cached_pool_metadata(self):
        data = {"config": {"pools": [configured(1)]},
                "pools": {"POOLS": [{"POOL": 0, "URL": "old.invalid", "User": "previous-owner"},
                                     {"POOL": 1, "URL": "removed.invalid", "User": "removed"}]}}
        values = pool_columns(data, "antminer")
        self.assertEqual(values["Worker"], "owner.worker-1")
        self.assertEqual(values["Pool2"], "")
        data["config"]["pools"] = []
        self.assertEqual(set(pool_columns(data, "antminer").values()), {""})

    def test_active_and_priority_do_not_reorder_configured_slots(self):
        data = {"pools": {"POOLS": [
            {"POOL": 0, "URL": "first.invalid", "User": "first", "Priority": 2, "Status": "Dead"},
            {"POOL": 1, "URL": "second.invalid", "User": "second", "Priority": 0, "Status": "Alive"},
            {"POOL": 2, "URL": "third.invalid", "User": "third", "Priority": 1},
            {"POOL": 3, "URL": "fee.invalid", "User": "fee"},
        ]}}
        for parser in ("vnish", "pitbit", "avalon", "ipollo", "generic"):
            with self.subTest(parser=parser):
                values = pool_columns(data, parser)
                self.assertEqual([values[k] for k in ("Worker", "Worker2", "Worker3")], ["first", "second", "third"])
                self.assertNotIn("fee", json.dumps(values))

    def test_invalid_and_duplicate_indexes_cannot_replace_another_slot(self):
        entries = [{"POOL": value, "URL": "bad.invalid", "User": "bad"}
                   for value in (False, True, -1, 3, 1.5, "bad", None, "1" * 5000)]
        entries += [{"POOL": 2, "User": "original"}, {"POOL": 2, "User": "duplicate"}]
        values = pool_columns({"pools": {"POOLS": entries}}, "generic")
        self.assertEqual([values[k] for k in ("Worker", "Worker2", "Worker3")], ["", "", "original"])

    def test_malformed_placeholder_keeps_later_array_position(self):
        values = pool_columns({"elphapex_config": {"pools": [configured(1), None, configured(3)]}}, "elphapex")
        self.assertEqual(values["Worker2"], "")
        self.assertEqual(values["Worker3"], "owner.worker-3")

    def test_whatsminer_rpc3_and_legacy_wrappers(self):
        entries = [{"index": 2, "url": "third.invalid", "account": "rpc.third"},
                   {"index": 0, "url": "first.invalid", "account": "rpc.first"}]
        for reply in ({"code": 0, "msg": {"pools": entries}},
                      {"code": 0, "Msg": {"pools": entries}}, {"POOLS": entries}):
            with self.subTest(reply=reply):
                values = pool_columns({"rpc_pools": reply}, "whatsminer")
                self.assertEqual([values[k] for k in ("Worker", "Worker2", "Worker3")], ["rpc.first", "", "rpc.third"])

    def test_jasminer_and_goldshell_lists(self):
        entries = [configured(1), configured(2), configured(3)]
        for parser, data in (("jasminer", {"jasminer_status": {"pools": {"pool": entries}}}),
                             ("jasminer", {"jasminer_status": {"pools": entries}}),
                             ("goldshell", {"goldshell_pools": entries})):
            with self.subTest(parser=parser, data=data):
                self.assertEqual(pool_columns(data, parser)["Worker3"], "owner.worker-3")

    def test_vnish_rest_pool_array_is_used_when_cgminer_is_unavailable(self):
        entries = [vnish_pool(0), vnish_pool(1), vnish_pool(2)]
        for root in ({"miner": {"pools": entries}}, {"pools": entries}):
            self.assertEqual(pool_columns({"vnish_summary": root}, "vnish")["Worker3"], "owner.worker-3")

    def test_vnish_current_rest_slots_override_cached_cgminer_values(self):
        data = {"vnish_summary": {"miner": {"pools": [vnish_pool(0), vnish_pool(1), vnish_pool(2)]}},
                "pools": {"POOLS": [{"POOL": 0, "URL": "old.invalid", "User": "old.worker"}]}}
        original = deepcopy(data)
        values = pool_columns(data, "vnish")
        self.assertEqual(values["Worker"], "owner.worker-1")
        self.assertEqual(values["Worker3"], "owner.worker-3")
        self.assertEqual(data, original)

    def test_vnish_service_pools_do_not_displace_customer_slots_or_close_holes(self):
        data = {"vnish_summary": {"miner": {"pools": [
            vnish_pool(3, pool_type="DevFee", user="firmware-fee"),
            vnish_pool(2), vnish_pool(4, pool_type="Refund", user="firmware-refund"), vnish_pool(0),
        ]}}}
        values = pool_columns(data, "vnish")
        self.assertEqual([values[k] for k in ("Worker", "Worker2", "Worker3")], ["owner.worker-1", "", "owner.worker-3"])
        self.assertNotIn("firmware-", json.dumps(values))

    def test_vnish_explicit_empty_current_pools_do_not_show_old_cached_accounts(self):
        for summary in ({"miner": {"pools": []}}, {"pools": []}):
            values = pool_columns({"vnish_summary": summary,
                                   "pools": {"POOLS": [{"POOL": 0, "User": "old.worker"}]}}, "vnish")
            self.assertEqual(set(values.values()), {""})

    def test_vnish_foreign_or_malformed_summary_does_not_replace_valid_cgminer(self):
        invalid_entries = [None, "bad", {}, [None], [{"id": 0}], [configured(1)],
                           [vnish_pool(True)], [vnish_pool(-1)], [vnish_pool(0, id="0")],
                           [vnish_pool(0, user=None)], [vnish_pool(0, pool_type="unknown")],
                           [vnish_pool(0), {"unrelated": "metadata"}]]
        for entries in invalid_entries:
            with self.subTest(entries=entries):
                values = pool_columns({"vnish_summary": {"miner": {"pools": entries}},
                                       "pools": {"POOLS": [{"POOL": 0, "User": "fallback.worker"}]}}, "vnish")
                self.assertEqual(values["Worker"], "fallback.worker")

    def test_vnish_id_is_not_interpreted_as_slot_for_other_firmware(self):
        values = pool_columns({"goldshell_pools": [{"id": 2, "url": "first.invalid", "user": "first.worker"}]}, "goldshell")
        self.assertEqual(values["Worker"], "first.worker")
        self.assertEqual(values["Worker3"], "")

    def test_password_fields_and_embedded_url_credentials_are_not_persisted(self):
        entries = [configured(1), configured(2), configured(3)]
        entries[0]["url"] = "stratum+tcp://private-user:private-secret@pool.invalid:3333"
        entries[1]["url"] = "private-user:private-secret@backup.invalid:4444"
        entries[2]["worker"] = {"token": "private-secret"}
        entries[2].pop("user")
        source = {"config": {"pools": entries}}
        original = deepcopy(source)
        values = pool_columns(source, "antminer")
        serialized = json.dumps(values)
        self.assertNotIn("private-secret", serialized)
        self.assertNotIn("private-user", serialized)
        self.assertNotIn("do-not-store", serialized)
        self.assertEqual(values["Pool"], "stratum+tcp://pool.invalid:3333")
        self.assertEqual(values["Pool2"], "backup.invalid:4444")
        self.assertEqual(values["Worker3"], "")
        self.assertEqual(source, original)

    def test_invalid_url_and_non_text_fields_do_not_leak_python_objects(self):
        values = pool_columns({"goldshell_pools": [
            {"url": "stratum+tcp://[invalid-host", "user": None},
            {"url": {"password": "secret"}, "user": False},
        ]}, "goldshell")
        self.assertEqual(set(values.values()), {""})

    def test_absent_and_malformed_responses_remain_empty(self):
        for parser in ("antminer", "vnish", "whatsminer", "elphapex", "jasminer", "goldshell", "avalon", "ipollo", "generic"):
            for value in (None, {}, "bad", [None]):
                data = {"config": value, "pools": value, "rpc_pools": value,
                        "jasminer_status": value, "goldshell_pools": value}
                with self.subTest(parser=parser, value=value):
                    projection = pool_columns(data, parser)
                    self.assertEqual(set(projection), set(POOL_FIELDS))
                    self.assertEqual(set(projection.values()), {""})

    def test_existing_custom_parser_first_slot_can_be_retained(self):
        values = pool_columns({}, "generic", {"Pool": "custom.invalid", "Worker": "custom.worker"})
        self.assertEqual(values["Worker"], "custom.worker")
        self.assertEqual(values["Worker2"], "")

    def test_record_contains_all_slots_and_stale_export_blanks_them(self):
        data = stock()
        data["config"]["pools"] = [configured(1), configured(2), configured(3)]
        record = make_record(ProfileRegistry().resolve(data), "192.0.2.1", data)
        self.assertEqual(record.display["Worker3"], "owner.worker-3")
        self.assertNotIn("do-not-store", json.dumps(record.display))
        record.telemetry.stale = True
        output = record.to_legacy()
        self.assertEqual({output[key] for key in POOL_FIELDS}, {"—"})

    def test_local_repository_round_trip_preserves_slots_without_passwords(self):
        data = stock()
        data["config"]["pools"] = [configured(1), configured(2), configured(3)]
        record = make_record(ProfileRegistry().resolve(data), "192.0.2.1", data)
        with self.subTest("repository"):
            repository = DeviceRepository(":memory:")
            try:
                repository.save(record)
                restored = repository.get("192.0.2.1")
                self.assertEqual(restored.display["Worker3"], "owner.worker-3")
                serialized = repository._db().execute("SELECT record FROM devices").fetchone()[0]
                self.assertNotIn("do-not-store", serialized)
            finally:
                repository.close()

    def test_warm_poll_reads_updated_config_without_additional_requests(self):
        factory = FakeFactory()
        repository = DeviceRepository(":memory:")
        try:
            service = ScannerService(repository=repository, transport_factory=factory)
            service.poll("192.0.2.1")
            factory.data["config"]["pools"] = [configured(1), configured(2), configured(3)]
            factory.calls.clear()
            record = service.poll("192.0.2.1")
            self.assertEqual(record.display["Worker3"], "owner.worker-3")
            self.assertEqual([key for _, key in factory.calls],
                             ["stats", "summary", "config", "/cgi-bin/get_blink_status.cgi"])
            self.assertFalse(factory.writes)
        finally:
            repository.close()


if __name__ == "__main__":
    unittest.main()
