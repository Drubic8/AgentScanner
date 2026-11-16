"""Compatibility evidence preserves API structure without exporting settings."""
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from miner_scanner import pool_evidence as evidence
from miner_scanner.pool_backends import BackendState
from miner_scanner.pool_configuration import PoolEdit, PoolPlan, WorkerNaming, prepare_pool_batch, execute_pool_batch
from tests.test_pool_integration import OfflineDevice, OfflineService


IP = "192.0.2.73"
MAC = "02:AB:CD:EF:22:73"
SERIAL = "PRIVATE-SERIAL-773"
DEVICE_ID = "PRIVATE-DEVICE-334"
FINGERPRINT = "PRIVATE-FINGERPRINT-8899"
URL = "stratum+tcp://private-login:private-pass@private.example.invalid:3333"
WORKER = "PRIVATE-WORKER.003"
PASSWORD = "PRIVATE-POOL-PASSWORD-8890"
TOKEN = "PRIVATE-BEARER-TOKEN-1144"
UNKNOWN_KEY = "PRIVATE-DYNAMIC-KEY-2288"
UNKNOWN_VALUE = "PRIVATE-UNKNOWN-LEAF-99"
PRIVATE = (IP, MAC, SERIAL, DEVICE_ID, FINGERPRINT, URL, WORKER, PASSWORD, TOKEN, UNKNOWN_KEY, UNKNOWN_VALUE)


def identity():
    return {"make": "Bitmain", "model": "Antminer S21", "firmware": "Stock",
            "firmware_version": "Thu Sep 26 11:39:05 CST 2024", "api_version": "3.1",
            "profile_id": "bitmain.stock", "profile_version": 2,
            "ip": IP, "mac": MAC, "serial": SERIAL, "device_id": DEVICE_ID, "fingerprint": FINGERPRINT}


def record():
    return SimpleNamespace(identity=SimpleNamespace(**identity()))


def configuration():
    return {"pools": [{"url": URL, "user": WORKER, "pass": PASSWORD}, {},
                      {"url": "", "user": "", "pass": "********"}],
            "network": {"ip": IP, "hostname": "PRIVATE-HOSTNAME", "dhcp": False},
            "bitmain-work-mode": "0", "bitmain-fan-pwm": "73", "token": TOKEN,
            UNKNOWN_KEY: UNKNOWN_VALUE}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def node_count(shape):
    return 1 + sum(node_count(child) for child in shape.get("fields", {}).values()) + sum(
        node_count(child) for child in shape.get("items", []))


def nesting(shape):
    children = list(shape.get("fields", {}).values()) + shape.get("items", [])
    return 1 + max((nesting(child) for child in children), default=0)


def large_shape_input():
    # A compact DAG exercises a wide traversal without allocating a huge input.
    keys = ("miner", "network", "ui", "regional", "layout", "password", "url", "user", "pass",
            "URL", "User", "Password", "passwd", "worker", "account", "POOL", "index", "pool_index",
            "id", "pool_type", "priority", "Priority", "bitmain-work-mode", "miner-mode",
            "bitmain-freq-level", "freq-level", "bitmain-fan-ctrl", "bitmain-fan-pwm", "algo",
            "bitmain-freq", "bitmain-voltage", "bitmain-user-ip-cat", "fc-work-mode", "cooling",
            "misc", "overclock", "fan", "fans", "mode", "hostname", "ip", "gateway", "dns", "dhcp")
    item = {"url": PRIVATE[-1]}
    for _ in range(6):
        item = {key: item for key in keys}
    return item


class PoolEvidenceTests(unittest.TestCase):
    def assert_private_absent(self, value, extra=()):
        text = encoded(value)
        for private in (*PRIVATE, *extra):
            with self.subTest(private_kind=private.split("-", 2)[0]):
                self.assertNotIn(private, text)

    def test_configuration_exports_names_types_and_presence_only(self):
        config = configuration()
        original = deepcopy(config)
        result = evidence.configuration_evidence(config, "stock.cgi", record=record())
        report = evidence.evidence_report([result])
        self.assert_private_absent(report, ("PRIVATE-HOSTNAME", "private-login", "private-pass", "private.example.invalid"))
        shape = report["devices"][0]["configuration_schema"]
        self.assertEqual(shape["kind"], "object")
        self.assertEqual(shape["fields"]["bitmain-fan-pwm"], {"kind": "string"})
        self.assertNotIn(UNKNOWN_KEY, shape["fields"])
        self.assertEqual(shape["unknown_fields"], 2)  # dynamic key and token
        self.assertEqual(report["devices"][0]["pool_slots"], [
            {"position": 1, "fields": {"url": "present", "user": "present", "pass": "present"}},
            {"position": 2, "fields": {"url": "missing", "user": "missing", "pass": "missing"}},
            {"position": 3, "fields": {"url": "empty", "user": "empty", "pass": "masked"}}])
        self.assertEqual(config, original)

    def test_invalid_slot_values_are_marked_without_exporting_objects(self):
        config = {"pools": [{"url": {UNKNOWN_KEY: TOKEN}, "user": None, "pass": [PASSWORD]}, None,
                            {"URL": URL, "account": WORKER, "Password": PASSWORD}, {"url": URL}]}
        result = evidence.configuration_evidence(config)
        self.assert_private_absent(result)
        self.assertEqual(result["pool_slots"][0]["fields"], {"url": "invalid", "user": "invalid", "pass": "invalid"})
        self.assertEqual(result["pool_slots"][1]["fields"], {"url": "invalid", "user": "invalid", "pass": "invalid"})
        self.assertEqual(len(result["pool_slots"]), 3)
        self.assertTrue(result["configuration_schema"]["fields"]["pools"]["truncated"])

    def test_schema_digest_depends_on_types_and_not_leaf_values_or_dynamic_keys(self):
        first = configuration()
        second = deepcopy(first)
        second["pools"][0] = {"url": "other.invalid:4444", "user": "OTHER.55", "pass": "another-secret"}
        second["bitmain-fan-pwm"] = "100"
        second["network"] = {"ip": "198.51.100.55", "hostname": "OTHER-HOSTNAME", "dhcp": True}
        second.pop(UNKNOWN_KEY)
        second["ANOTHER-PRIVATE-DYNAMIC-KEY"] = "ANOTHER-PRIVATE-LEAF"
        def digest(config):
            return evidence.evidence_report([evidence.configuration_evidence(config)])["devices"][0]["schema_digest"]
        self.assertEqual(digest(first), digest(second))
        second["bitmain-fan-pwm"] = 100
        self.assertNotEqual(digest(first), digest(second))

    def test_current_technical_versions_and_dates_survive_redaction(self):
        for version in ("1.2.6", "1.2.3.4", "20260729.11.Rel2", "FR-1.12(251009-S21)",
                        "Thu Sep 26 11:39:05 CST 2024"):
            with self.subTest(version=version):
                row = identity()
                row.update(firmware_version=version, api_version="2.3.4.5")
                result = evidence.identity_evidence(row={"Identity": row})
                self.assertEqual(result["firmware_version"], version)
                self.assertEqual(result["api_version"], "2.3.4.5")
        for value in ("", None):
            result = evidence.identity_evidence(row={"Identity": {"firmware_version": value, "api_version": value}})
            self.assertIsNone(result["firmware_version"])
            self.assertIsNone(result["api_version"])

    def test_short_password_does_not_destroy_model_or_technical_version(self):
        result = evidence.identity_evidence(row={"Identity": {
            "model": "Antminer S21", "firmware_version": "1.2.6", "api_version": "3.1"}}, secrets=("1", "x"))
        self.assertEqual(result["model"], "Antminer S21")
        self.assertEqual(result["firmware_version"], "1.2.6")
        self.assertEqual(result["api_version"], "3.1")
        whole = evidence.identity_evidence(row={"Identity": {"model": "1"}}, secrets=("1",))
        self.assertNotEqual(whole["model"], "1")

    def test_identity_identifiers_are_redacted_when_echoed_into_other_fields(self):
        for private in (IP, MAC, SERIAL, DEVICE_ID, FINGERPRINT):
            with self.subTest(private_kind=private.split("-", 1)[0]):
                source = identity()
                source["model"] = "Antminer " + private
                source["firmware_version"] = private
                result = evidence.identity_evidence(row={"Identity": source})
                self.assertNotIn(private, encoded(result))
        self.assertEqual(evidence.identity_evidence(record())["profile_version"], 2)

    def test_pool_and_session_secrets_echoed_into_technical_identity_are_redacted(self):
        current = record()
        current.identity.model = "Antminer " + WORKER
        current.identity.firmware_version = PASSWORD
        current.identity.api_version = TOKEN
        state = BackendState("bitmain.stock.cgi", deepcopy(configuration()["pools"]), {
            "config": configuration(), "headers": {"Authorization": "Bearer " + TOKEN},
            "safe_evidence": {"api": {"settings_get": True, "settings_post": False, "raw": PASSWORD}}})
        result = evidence.evidence_from_state(state, current)
        self.assert_private_absent(result)
        self.assertEqual(result["api"], {"settings_get": True, "settings_post": False})

    def test_bare_bearer_and_decoded_url_credentials_are_redacted_across_fields(self):
        current = record()
        current.identity.model = "Antminer PRIVATE-URL-PASS"
        current.identity.firmware_version = TOKEN
        config = {"pools": [{"url": "stratum+tcp://PRIVATE%2DURL%2DLOGIN:PRIVATE%2DURL%2DPASS@pool.invalid:3333",
                             "user": WORKER, "pass": PASSWORD}]}
        state = BackendState("bitmain.stock.cgi", deepcopy(config["pools"]), {
            "config": config, "headers": {"Authorization": "Bearer " + TOKEN}})
        result = evidence.evidence_from_state(state, current)
        self.assert_private_absent(result, ("PRIVATE-URL-PASS", "PRIVATE-URL-LOGIN"))

    def test_unknown_backend_does_not_claim_vnish_read_source(self):
        state = BackendState("future.unreviewed.backend", [], {"config": configuration()})
        result = evidence.evidence_from_state(state, record())
        self.assertEqual(result["read_source"], "unknown")
        self.assertIsNone(result["backend"])

    def test_whatsminer_runtime_evidence_keeps_missing_passwords_and_redacts_salt(self):
        current = record()
        current.identity.firmware_version = 'PRIVATE-EPHEMERAL-SALT'
        slots = [{'url': URL, 'user': WORKER} for _ in range(3)]
        state = BackendState('whatsminer.rpc3.pools', slots, {
            'config': {'pools': slots, 'btrom-worker-suffix': ''},
            'ephemeral_secrets': ('PRIVATE-EPHEMERAL-SALT',),
            'verification_kind': 'runtime_without_passwords'})
        result = evidence.evidence_from_state(state, current)
        self.assertEqual(result['backend'], 'whatsminer.rpc3.pools')
        self.assertEqual(result['read_source'], 'whatsminer.rpc_pools')
        self.assertEqual(result['reading_kind'], 'runtime')
        self.assertEqual([slot['fields']['pass'] for slot in result['pool_slots']], ['missing'] * 3)
        self.assert_private_absent(result, ('PRIVATE-EPHEMERAL-SALT',))

    def test_elphapex_native_config_evidence_is_distinct_from_luci_read(self):
        slots = [{'url': URL, 'user': WORKER, 'pass': PASSWORD} for _ in range(3)]
        state = BackendState('elphapex.web.pools', slots, {
            'config': {'pools': slots, 'fc-work-mode': 0, 'fc-fan-ctrl': False,
                       'fc-voltage': '101', 'fc-freq': '501', 'fc-freq-level': '0',
                       'fc-fan-pwm': '90', 'algo': 'scrypt'}})
        result = evidence.evidence_from_state(state, record())
        self.assertEqual(result['backend'], 'elphapex.web.pools')
        self.assertEqual(result['read_source'], 'elphapex.web.conf')
        self.assertEqual(result['reading_kind'], 'saved_configuration')
        self.assertEqual([slot['fields']['pass'] for slot in result['pool_slots']], ['present'] * 3)
        self.assert_private_absent(result)

    def test_pitbit_evidence_preserves_hotel_schema_without_exporting_its_secrets(self):
        slots = [{'url': URL, 'user': WORKER, 'pass': PASSWORD} for _ in range(3)]
        hotel_password = 'PRIVATE-HOTEL-POOL-PASSWORD'
        current = record()
        current.identity.firmware_version = hotel_password
        config = {'pools': [dict(slot, index=i) for i, slot in enumerate(slots)],
                  'poolshotelfee': [{'url': URL, 'user': WORKER, 'pass': hotel_password}],
                  'hotelfeepercent': '17', 'need_update_fw_now': False}
        state = BackendState('pitbit.c096.web.pools', slots, {'config': config})
        result = evidence.evidence_from_state(state, current)
        self.assertEqual(result['backend'], 'pitbit.c096.web.pools')
        self.assertEqual(result['read_source'], 'pitbit.web.conf')
        self.assertEqual(result['reading_kind'], 'saved_configuration')
        fields = result['configuration_schema']['fields']
        self.assertEqual(fields['poolshotelfee']['kind'], 'array')
        self.assertEqual(fields['poolshotelfee']['length'], 1)
        self.assertEqual(fields['need_update_fw_now']['kind'], 'boolean')
        self.assert_private_absent(result, (hotel_password,))

    def test_provided_evidence_is_rewhitelisted_and_detached(self):
        source = {"identity": {**identity(), "model": "Antminer " + SERIAL}, "review_status": WORKER,
                  "result_status": TOKEN, "backend": URL, "read_source": PASSWORD,
                  "configuration_schema": {"kind": "object", "fields": {
                      UNKNOWN_KEY: {"kind": "string", "raw": TOKEN},
                      "pools": {"kind": "array", "length": 10, "items": [
                          {"kind": "string", "raw": PASSWORD}, {"kind": URL}]}}, "unknown_fields": 1,
                      "raw_response": configuration()},
                  "pool_slots": [{"position": IP, "fields": {"url": URL, "user": "present", "pass": PASSWORD}}],
                  "api": {"settings_get": TOKEN, "settings_post": True, "servers": URL},
                  "raw_config": configuration()}
        original = deepcopy(source)
        result = evidence.evidence_report([source])["devices"][0]
        self.assert_private_absent(result)
        self.assertEqual(result["review_status"], "failed")
        self.assertIsNone(result["backend"])
        self.assertEqual(result["read_source"], "unknown")
        self.assertEqual(result["api"], {"settings_get": False, "settings_post": True})
        self.assertEqual(result["pool_slots"][0], {"position": 1, "fields": {
            "url": "invalid", "user": "present", "pass": "invalid"}})
        result["configuration_schema"]["fields"].clear()
        self.assertEqual(source, original)

    def test_invalid_input_types_and_unhashable_source_are_safe(self):
        for value in (None, [], "private", 4, True, object(), {1: PASSWORD, (2, 3): TOKEN}):
            with self.subTest(value_type=type(value).__name__):
                report = evidence.evidence_report([evidence.configuration_evidence(value)])
                self.assert_private_absent(report)
        for source in ([], {}, None, True):
            with self.subTest(source_type=type(source).__name__):
                self.assertEqual(evidence.configuration_evidence({}, source)["read_source"], "unknown")
        for bad in (None, [], 1, "private"):
            self.assert_private_absent(evidence.sanitize_evidence(bad))

    def test_depth_node_and_cycle_bounds(self):
        config = large_shape_input()
        shape = evidence.configuration_evidence(config)["configuration_schema"]
        self.assertLessEqual(node_count(shape), 256)
        self.assertLessEqual(nesting(shape), 8)
        cycle = {}
        cycle["miner"] = cycle
        detached = evidence.configuration_evidence(cycle, record=record())
        self.assertLessEqual(node_count(detached["configuration_schema"]), 256)
        self.assertLess(len(encoded(detached)), 10000)
        self.assert_private_absent(detached)
        # A supplied detached schema can also be cyclic; export must re-bound it.
        cyclic_shape = {"kind": "object", "fields": {}}
        cyclic_shape["fields"]["miner"] = cyclic_shape
        result = evidence.sanitize_evidence({"configuration_schema": cyclic_shape})
        self.assertLessEqual(node_count(result["configuration_schema"]), 256)

    def test_api_evidence_copies_only_known_operation_presence(self):
        result = evidence.api_evidence({"servers": [{"url": URL}], "paths": {
            "/settings": {"get": {"description": PASSWORD}, "post": {"example": TOKEN}},
            URL: {"get": {"private": WORKER}}}})
        self.assertEqual(result, {"settings_get": True, "settings_post": True})
        self.assert_private_absent(result)

    def test_report_device_count_limit_and_anonymous_numbering(self):
        reports = evidence.evidence_report([{}, {}])
        self.assertEqual([device["device"] for device in reports["devices"]], ["device-001", "device-002"])
        self.assertIs(reports["hardware_verified"], False)
        with self.assertRaises(ValueError):
            evidence.evidence_report({} for _ in range(4097))

    def test_report_encoded_size_limit_is_enforced_below_device_count_limit(self):
        candidate = evidence.configuration_evidence(large_shape_input())
        one = evidence.evidence_report([candidate])["devices"][0]
        item_bytes = len(json.dumps(one, ensure_ascii=False).encode("utf-8"))
        count = (16 * 1024 * 1024) // item_bytes + 2
        self.assertLessEqual(count, 4096)
        with self.assertRaisesRegex(ValueError, "too large"):
            evidence.evidence_report(candidate for _ in range(count))

    def test_plan_identity_fallback_masks_known_identifiers_without_reading_private_state(self):
        class PrivateState:
            def __getattribute__(self, name):
                raise AssertionError("Report inspected private prepared settings")
        plan = PoolPlan(IP, device_id=DEVICE_ID, profile_id="bitmain.stock", model="Antminer " + DEVICE_ID,
                        firmware="Stock", firmware_version="1.2.3.4", status="ready", _prepared=PrivateState())
        report = evidence.evidence_report([plan])
        self.assert_private_absent(report)
        self.assertEqual(report["devices"][0]["identity"]["firmware_version"], "1.2.3.4")

    def test_export_performs_no_network_file_or_credential_access(self):
        # All imports precede the guard; the support-report operation itself
        # must not inspect local profiles, credentials or any remote device.
        import miner_scanner.service
        import miner_scanner.access
        with patch("builtins.open", side_effect=AssertionError("file access")), \
             patch("pathlib.Path.open", side_effect=AssertionError("path access")), \
             patch("socket.create_connection", side_effect=AssertionError("socket access")), \
             patch("requests.sessions.Session.request", side_effect=AssertionError("HTTP access")), \
             patch("miner_scanner.service.ScannerService.credentials_for", side_effect=AssertionError("credential access")), \
             patch("miner_scanner.access.AccessProfiles.candidates", side_effect=AssertionError("profile access")):
            result = evidence.evidence_report([evidence.configuration_evidence(configuration(), "stock.cgi", record=record())])
        self.assert_private_absent(result)


class PoolEvidenceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.ip = "192.0.2.10"
        self.device = OfflineDevice()
        self.service = OfflineService({self.ip: self.device})
        self.addCleanup(self.service.repository.close)

    def prepare(self, edits=None):
        return list(prepare_pool_batch(self.service, [self.service.records[self.ip].to_legacy()],
            edits or [PoolEdit(0, worker=WorkerNaming("TEST"))]))

    def test_unsupported_stock_model_keeps_safe_read_schema_and_versions(self):
        current = self.service.records[self.ip]
        current.identity.model = "Antminer S19"
        current.identity.firmware_version = "Tue Dec 6 16:10:00 CST 2022"
        self.device.config[UNKNOWN_KEY] = UNKNOWN_VALUE
        plans = self.prepare()
        self.assertEqual(plans[0].status, "unsupported")
        report = evidence.evidence_report(plans)
        device = report["devices"][0]
        self.assertEqual(device["review_status"], "unsupported")
        self.assertEqual(device["read_source"], "stock.cgi")
        self.assertEqual(device["identity"]["model"], "Antminer S19")
        self.assertEqual(device["identity"]["firmware_version"], "Tue Dec 6 16:10:00 CST 2022")
        self.assertEqual(device["configuration_schema"]["fields"]["pools"]["kind"], "array")
        self.assertEqual(len(device["schema_digest"]), 64)
        self.assertNotIn(UNKNOWN_KEY, encoded(report))
        self.assertNotIn(UNKNOWN_VALUE, encoded(report))
        self.assertFalse(self.device.writes)

    def test_ready_success_disposed_private_plan_and_unchanged_plan_keep_safe_evidence(self):
        plans = self.prepare()
        self.assertEqual(plans[0].status, "ready")
        before = evidence.evidence_report(plans)["devices"]
        result = dict(execute_pool_batch(self.service, plans))[self.ip]
        self.assertEqual(result.status, "succeeded")
        disposed = replace(plans[0], _prepared=None)
        self.assertEqual(evidence.evidence_report([disposed])["devices"], before)
        unchanged = self.prepare()
        self.assertEqual(unchanged[0].status, "unchanged")
        public = evidence.evidence_report([disposed, *unchanged])
        text = encoded(public)
        for secret in (self.ip, "TEST.1", "OWNER.old1", "BACKUP.old3", "pool-secret1", "pool-secret3"):
            self.assertNotIn(secret, text)
        self.assertEqual(public["devices"][1]["review_status"], "unchanged")
        self.assertIn("configuration_schema", public["devices"][1])

    def test_failed_read_keeps_anonymous_technical_identity_without_exception_text(self):
        self.service.poll = lambda *args, **kwargs: (_ for _ in ()).throw(OSError(PASSWORD))
        plans = self.prepare()
        self.assertEqual(plans[0].status, "offline")
        report = evidence.evidence_report(plans)
        self.assertEqual(report["devices"][0]["review_status"], "offline")
        self.assertEqual(report["devices"][0]["identity"]["model"], "Antminer L9")
        self.assertNotIn(PASSWORD, encoded(report))
        self.assertNotIn(self.ip, encoded(report))


if __name__ == "__main__":
    unittest.main()
