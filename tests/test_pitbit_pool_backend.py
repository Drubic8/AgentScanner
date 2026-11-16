"""C096 full-config contract joined to the real pool core without networking."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import requests

from miner_scanner import pool_backends as backends, pitbit_pool_backend as pitbit
from miner_scanner.models import Credentials, DeviceIdentity, DeviceRecord, TelemetrySnapshot
from miner_scanner.drivers import make_record
from miner_scanner.pool_configuration import PoolEdit, WorkerNaming, prepare_pool_batch, execute_pool_batch
from miner_scanner.pool_evidence import evidence_from_state
from miner_scanner.profiles import ProfileRegistry
from miner_scanner.runtime import Operation, ProtocolError
from tests.test_pool_backends import record
from tests.test_pool_integration import OfflineService


FIXTURE = Path(__file__).parent / "fixtures" / "pitbit_c096_pools_read.json"


class PitbitDevice:
    firmware = "PitBit"

    def __init__(self, fixture):
        self.config = deepcopy(fixture["config"])
        self.system = deepcopy(fixture["synthetic_system"])
        self.version = deepcopy(fixture["native_version"])
        self.static = {path: ("/* synthetic asset " + path + " */").encode()
                       for path in fixture["static_hashes"]}
        self.calls, self.writes = [], []
        self.reply = (200, b'{"stats":"success","code":"M000"}')
        self.apply = True
        self.error = None


class PitbitTransport:
    def __init__(self, device):
        self.device = device

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def cgminer(self, command):
        if command != "version":
            raise AssertionError("Only the reviewed native version read is allowed")
        self.device.calls.append(("native4028:version", "READ"))
        return deepcopy(self.device.version)

    def http_json(self, path, method="GET", **kwargs):
        status, raw = self.http(path, method, **kwargs)
        return json.loads(raw) if status == 200 else None

    def http(self, path, method="GET", **kwargs):
        device = self.device
        device.calls.append((path, method))
        if method == "GET":
            if path == pitbit.CONFIG_PATH:
                return 200, json.dumps(device.config, ensure_ascii=False).encode()
            if path == pitbit.SYSTEM_PATH:
                return 200, json.dumps(device.system).encode()
            if path in device.static:
                return 200, device.static[path]
            raise AssertionError("Unreviewed PitBit read")
        if method != "POST" or path != pitbit.SETTER_PATH:
            raise AssertionError("Unreviewed PitBit write/control")
        if kwargs.get("headers") != {"Content-Type": "application/json"}:
            raise AssertionError("The React form posts JSON")
        outgoing = deepcopy(kwargs["payload"])
        device.writes.append((path, method, outgoing))
        if device.apply:
            device.config = deepcopy(outgoing)
        if device.error:
            raise device.error
        return device.reply


class PitbitPoolBackendTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.device = PitbitDevice(self.fixture)
        identity = record("Bitmain", "PitBit", "Antminer S21+_i").identity
        identity.serial, identity.mac = "synthetic-scanner-serial", self.device.system["macaddr"].upper()
        self.record = DeviceRecord(DeviceIdentity(**vars(identity)), TelemetrySnapshot(), {})
        for key, value in self.fixture["identity"].items():
            setattr(self.record.identity, key, value)
        self.credentials = Credentials("synthetic-web-user", "synthetic-web-password")
        self.service = SimpleNamespace(credentials_for=lambda _: self.credentials)
        self.transport = PitbitTransport(self.device)
        self.assertEqual(pitbit.STATIC_HASHES, self.fixture["static_hashes"])
        hashes = {path: hashlib.sha256(body).hexdigest() for path, body in self.device.static.items()}
        digest_patch = patch.object(pitbit, "STATIC_HASHES", hashes)
        digest_patch.start()
        self.addCleanup(digest_patch.stop)
        for target in ("requests.sessions.Session.request", "socket.create_connection"):
            guard = patch(target, side_effect=AssertionError("Actual network use is forbidden"))
            guard.start()
            self.addCleanup(guard.stop)

    def read(self):
        return backends.read_state(self.service, self.record, self.transport)

    def core_service(self):
        service = OfflineService({self.record.identity.ip: self.device})
        self.addCleanup(service.repository.close)
        service.records[self.record.identity.ip] = self.record
        service.credentials_for = self.service.credentials_for
        service.transport_factory = lambda *_: self.transport
        return service

    def test_observed_shapes_canonical_slots_and_native_identity_are_separate_from_wrapper(self):
        provenance = self.fixture["provenance"]
        self.assertTrue(provenance["read_structure_observed"])
        self.assertTrue(provenance["all_configuration_values_are_synthetic"])
        self.assertFalse(provenance["hardware_write_verified"])
        self.assertEqual(provenance["setter_requests_sent_in_observation"], 0)
        self.assertEqual(len(self.fixture["config"]), 23)
        state = self.read()
        self.assertEqual(state.backend_id, "pitbit.c096.web.pools")
        self.assertEqual([set(slot) for slot in state.slots], [{"url", "user", "pass"}] * 3)
        self.assertEqual([slot["index"] for slot in state.opaque["config"]["pools"]], [0, 1, 2])
        self.assertEqual(self.device.calls, [(pitbit.CONFIG_PATH, "GET"), ("native4028:version", "READ"),
            (pitbit.SYSTEM_PATH, "GET")] + [(path, "GET") for path in pitbit.STATIC_HASHES])
        self.assertEqual(self.record.identity.firmware_version, "incm.C096")
        self.assertNotEqual(self.record.identity.firmware_version,
                            self.device.system["system_filesystem_version"])
        self.assertFalse(self.device.writes)

    def test_payload_merges_only_three_strings_preserving_indices_hotel_and_all_types(self):
        before = deepcopy(self.device.config)
        state = self.read()
        slots = deepcopy(state.slots)
        password = "  synthetic:пароль,1 ***  "
        slots[0].update(user="TEST.001", **{"pass": password})
        backends.validate_state(state, slots)
        outgoing = backends._write_payload(state, slots)
        expected = deepcopy(before)
        expected["pools"][0].update(user="TEST.001", **{"pass": password})
        self.assertEqual(outgoing, expected)
        self.assertEqual(outgoing["poolshotelfee"], before["poolshotelfee"])
        self.assertEqual(outgoing["hotelfeepercent"], "037")
        self.assertEqual(outgoing["pools"][1:], before["pools"][1:])
        for key in before:
            self.assertIs(type(outgoing[key]), type(before[key]), key)
        self.assertEqual([slot["index"] for slot in outgoing["pools"]], [0, 1, 2])
        self.assertEqual(self.device.config, before)
        self.assertEqual(state.opaque["config"], before)
        self.assertNotIn(password, repr(state))
        self.assertNotIn("synthetic-hotel-password", repr(state))

    def test_acl_wildcards_and_explicit_empty_password_are_preserved(self):
        self.device.config["api-groups"] = "*"
        state = self.read()
        slots = deepcopy(state.slots)
        slots[2]["pass"] = ""
        outgoing = backends._write_payload(state, slots)
        self.assertEqual((outgoing["api-allow"], outgoing["api-groups"]), ("*", "*"))
        self.assertEqual(outgoing["pools"][2]["pass"], "")
        self.assertEqual(self.device.config["pools"][2]["pass"], "synthetic-pitbit-password-3")

    def test_wrong_record_identity_and_c074_never_inherit_current_contract(self):
        baseline = deepcopy(self.record.identity)
        for field, value in (("model", "Antminer S21_i"), ("model", "Antminer S21+"),
            ("firmware_version", "incm.C074"), ("firmware_version", "unreviewed-wrapper-build"),
            ("api_version", "3.0"),
            ("profile_id", "bitmain.pitbit.s21.c074.20240202"), ("firmware", "Stock"),
            ("make", "Other")):
            with self.subTest(field=field, value=value):
                self.record.identity = deepcopy(baseline)
                setattr(self.record.identity, field, value)
                self.device.calls.clear()
                with self.assertRaises(backends.PoolBackendError):
                    self.read()
                self.assertFalse(self.device.calls)

    def test_wrong_native_version_model_api_or_error_status_refuse(self):
        baseline = deepcopy(self.device.version)
        for field, value in (("Miner", "incm.C074"), ("Type", "Antminer S21_i"),
                             ("API", "3.0"), ("STATUS", "E"), ("STATUS", None), ("STATUS", [])):
            with self.subTest(field=field):
                self.device.version = deepcopy(baseline)
                if field == "STATUS":
                    if value is None:
                        self.device.version.pop("STATUS")
                    elif value == []:
                        self.device.version["STATUS"] = []
                    else:
                        self.device.version["STATUS"][0]["STATUS"] = value
                else:
                    self.device.version["VERSION"][0][field] = value
                with self.assertRaises(backends.PoolBackendError):
                    self.read()
                self.assertFalse(self.device.writes)

    def test_wrong_main_or_html_hash_refuses_with_safe_evidence(self):
        for path in self.device.static:
            with self.subTest(path=path):
                body = self.device.static[path]
                self.device.static[path] += b"unreviewed"
                try:
                    with self.assertRaises(backends.PoolBackendError) as context:
                        self.read()
                    self.assertTrue(hasattr(context.exception, "pool_evidence"))
                finally:
                    self.device.static[path] = body
        self.assertFalse(self.device.writes)

    def test_unknown_missing_or_wrong_typed_config_and_indices_refuse(self):
        original = deepcopy(self.device.config)
        for kind in ("unknown", "missing", "bool_mode", "string_flag", "missing_pass", "hotel_index",
                     "missing_hotel_pass", "bool_index", "duplicate_index", "reordered_index"):
            with self.subTest(kind=kind):
                self.device.config = deepcopy(original)
                if kind == "unknown":
                    self.device.config["unknown-setting"] = "synthetic"
                elif kind == "missing":
                    self.device.config.pop("target-temp")
                elif kind == "bool_mode":
                    self.device.config["bitmain-work-mode"] = True
                elif kind == "string_flag":
                    self.device.config["need_update_fw_now"] = "false"
                elif kind == "missing_pass":
                    self.device.config["pools"][2].pop("pass")
                elif kind == "hotel_index":
                    self.device.config["poolshotelfee"][0]["index"] = 0
                elif kind == "missing_hotel_pass":
                    self.device.config["poolshotelfee"][0].pop("pass")
                elif kind == "bool_index":
                    self.device.config["pools"][0]["index"] = False
                elif kind == "duplicate_index":
                    self.device.config["pools"][1]["index"] = 0
                else:
                    self.device.config["pools"].reverse()
                with self.assertRaises(backends.PoolBackendError):
                    self.read()
                self.assertFalse(self.device.writes)

    def test_upgrade_flag_blocks_read_validate_and_fresh_prewrite_without_mutation(self):
        state = self.read()
        self.device.config["need_update_fw_now"] = True
        current = deepcopy(self.device.config)
        with self.assertRaises(backends.PoolBackendError):
            self.read()
        candidate = deepcopy(state)
        candidate.opaque["config"]["need_update_fw_now"] = True
        with self.assertRaises(backends.PoolBackendError):
            backends.validate_state(candidate, candidate.slots)
        with self.assertRaises(backends.PoolBackendError):
            backends.write_state(self.service, self.record, self.transport, state, state.slots)
        self.assertEqual(self.device.config, current)
        self.assertFalse(self.device.writes)

    def test_masked_user_or_hotel_credentials_block_without_erasing(self):
        state = self.read()
        for kind in ("owner", "hotel"):
            with self.subTest(kind=kind):
                candidate = deepcopy(state)
                slots = deepcopy(state.slots)
                if kind == "owner":
                    slots[1]["pass"] = "********"
                else:
                    candidate.opaque["config"]["poolshotelfee"][0]["pass"] = "••••"
                with self.assertRaises(backends.PoolBackendError):
                    backends.validate_state(candidate, slots)
                self.assertFalse(self.device.writes)

    def test_missing_credentials_and_changed_known_hardware_block(self):
        credentials = self.credentials
        self.credentials = None
        with self.assertRaises(backends.PoolBackendError) as context:
            self.read()
        self.assertEqual(context.exception.status, "auth_required")
        self.assertFalse(self.device.calls)
        self.credentials = credentials
        self.device.system["macaddr"] = "02:00:00:00:00:99"
        with self.assertRaises(backends.PoolBackendError) as context:
            self.read()
        self.assertEqual(context.exception.status, "changed")
        self.assertFalse(self.device.writes)

    def test_fresh_prewrite_preserves_hotel_and_detects_outside_changes(self):
        state = self.read()
        self.device.config["poolshotelfee"][0]["user"] = "synthetic-external-change"
        with self.assertRaises(backends.PoolBackendError) as context:
            backends.write_state(self.service, self.record, self.transport, state, state.slots)
        self.assertEqual(context.exception.status, "changed")
        self.assertFalse(self.device.writes)

    def test_reviewed_ack_and_explicit_rejection_are_not_transport_guesses(self):
        for status, raw, expected in ((200, b'{"stats":"success"}', True),
            (201, b'{"stats":"success","code":"M000"}', True),
            (200, b'{"stats":"error","code":"M028","msg":"firmware upgrade process"}', False),
            (400, b"rejected", False), (200, b"{}", None), (200, b"not json", None),
            (200, b'{"stats":false}', None), (200, b'{"stats":"unrecognized"}', None),
            (200, b'{"stats":"success","error":true}', False), (500, b'{"stats":"success"}', None)):
            with self.subTest(status=status, raw=raw):
                self.assertIs(pitbit._accepted(status, raw), expected)

    def test_exactly_one_post_and_unknown_transport_outcome_never_retries(self):
        state = self.read()
        for error in (requests.ReadTimeout("synthetic-private-response"), ProtocolError("synthetic-private-response")):
            with self.subTest(error=type(error).__name__):
                self.device.writes.clear()
                self.device.error = error
                self.assertIsNone(backends.write_state(self.service, self.record, self.transport, state, state.slots))
                self.assertEqual(len(self.device.writes), 1)

    def test_evidence_keeps_shape_and_hides_owner_hotel_and_web_secrets(self):
        state = self.read()
        safe = evidence_from_state(state, self.record)
        self.assertEqual(safe["read_source"], "pitbit.web.conf")
        self.assertEqual(safe["reading_kind"], "saved_configuration")
        text = json.dumps(safe, ensure_ascii=False)
        for secret in (self.credentials.password, self.device.config["pools"][0]["pass"],
                       self.device.config["poolshotelfee"][0]["pass"], self.device.system["macaddr"]):
            self.assertNotIn(secret, text)

    def test_real_core_prepares_common_prefix_and_full_typed_readback(self):
        service = self.core_service()
        initial = deepcopy(self.device.config)
        before = self.read()
        password = "  synthetic:пароль,1 ***  "
        edits = [PoolEdit(0, worker=WorkerNaming("TEST", width=3), password=password)]
        plans = list(prepare_pool_batch(service, [self.record.to_legacy()], edits))
        self.assertEqual(plans[0].status, "ready", plans[0].message)
        self.assertFalse(self.device.writes)
        result = dict(execute_pool_batch(service, plans))[self.record.identity.ip]
        self.assertEqual(result.status, "succeeded", result.message)
        self.assertTrue(result.accepted_by_api)
        expected = deepcopy(initial)
        expected["pools"][0].update(user="TEST.001", **{"pass": password})
        self.assertEqual(self.device.config, expected)
        self.assertEqual(self.device.writes, [(pitbit.SETTER_PATH, "POST", expected)])
        after = self.read()
        self.assertTrue(backends.equivalent_state(before, after, ignore_pools=True))
        self.assertFalse(backends.equivalent_state(before, after))
        self.assertNotIn(password, repr(plans))
        self.assertNotIn(password, repr(result))

    def test_real_driver_http_build_candidate_requires_native_c096_and_runs_full_core(self):
        profile = ProfileRegistry().by_id["bitmain.pitbit"]
        data = {"version": deepcopy(self.fixture["native_version"]),
                "system": deepcopy(self.fixture["synthetic_system"])}
        actual = make_record(profile, self.record.identity.ip, data)
        self.assertEqual(actual.identity.model, pitbit.ENGINE_TYPE)
        self.assertEqual(actual.identity.api_version, pitbit.ENGINE_API)
        self.assertEqual(actual.identity.firmware_version, pitbit.HTTP_WRAPPER_VERSION)
        self.assertNotEqual(actual.identity.firmware_version, pitbit.ENGINE_VERSION)
        self.record = actual
        service = self.core_service()
        baseline = deepcopy(self.device.config)
        edits = [PoolEdit(0, worker=WorkerNaming("TEST", width=3))]

        # The wrapper build alone never qualifies C074 for a current pool write.
        self.device.version["VERSION"][0]["Miner"] = "incm.C074"
        refused = list(prepare_pool_batch(service, [actual.to_legacy()], edits))[0]
        self.assertEqual(refused.status, "changed", refused.message)
        self.assertIsNone(refused._prepared)
        self.assertFalse(self.device.writes)

        self.device.version = deepcopy(self.fixture["native_version"])
        plan = list(prepare_pool_batch(service, [actual.to_legacy()], edits))[0]
        self.assertEqual(plan.status, "ready", plan.message)
        self.assertEqual(actual.identity.firmware_version, pitbit.HTTP_WRAPPER_VERSION)
        self.assertFalse(self.device.writes)
        result = dict(execute_pool_batch(service, [plan]))[actual.identity.ip]
        self.assertEqual(result.status, "succeeded", result.message)
        self.assertTrue(result.accepted_by_api)
        expected = deepcopy(baseline)
        expected["pools"][0]["user"] = "TEST.001"
        self.assertEqual(self.device.writes, [(pitbit.SETTER_PATH, "POST", expected)])
        self.assertEqual(self.device.config, expected)

    def test_real_core_timeout_is_unconfirmed_without_replay(self):
        service = self.core_service()
        pause = patch.object(Operation, "pause", return_value=None)
        pause.start()
        self.addCleanup(pause.stop)
        plans = list(prepare_pool_batch(service, [self.record.to_legacy()], [PoolEdit(0, worker=WorkerNaming("TEST"))]))
        self.assertEqual(plans[0].status, "ready", plans[0].message)
        self.device.apply = False
        self.device.error = requests.ReadTimeout("synthetic-private-response")
        result = dict(execute_pool_batch(service, plans))[self.record.identity.ip]
        self.assertEqual(result.status, "unconfirmed", result.message)
        self.assertFalse(result.accepted_by_api)
        self.assertEqual(len(self.device.writes), 1)
        self.assertEqual(dict(execute_pool_batch(service, plans))[self.record.identity.ip], result)
        self.assertEqual(len(self.device.writes), 1)


if __name__ == "__main__":
    unittest.main()
