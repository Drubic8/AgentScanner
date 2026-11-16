"""Observed Elphapex web contract through offline HTTP and real pool planning."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import requests

from miner_scanner import pool_backends as backends, elphapex_pool_backend as elpha
from miner_scanner.models import Credentials, DeviceIdentity, DeviceRecord, TelemetrySnapshot
from miner_scanner.pool_configuration import PoolEdit, WorkerNaming, prepare_pool_batch, execute_pool_batch
from miner_scanner.pool_evidence import evidence_from_state
from miner_scanner.runtime import Operation, ProtocolError
from tests.test_pool_integration import OfflineService
from tests.test_pool_backends import record


FIXTURE = Path(__file__).parent / "fixtures" / "elphapex_dg1plus_web_pools_read.json"


class ElphaDevice:
    firmware = "Stock"

    def __init__(self, fixture):
        self.config = deepcopy(fixture["config"])
        self.system = deepcopy(fixture["synthetic_system"])
        self.static = {path: ("/* synthetic body for " + path + " */").encode()
                       for path in fixture["static_hashes"]}
        self.calls, self.writes = [], []
        self.reply = (200, b"{}")
        self.apply = True
        self.error = None


class ElphaTransport:
    def __init__(self, device, *args):
        self.device = device

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def http_json(self, path, method="GET", **kwargs):
        status, raw = self.http(path, method, **kwargs)
        return json.loads(raw) if status == 200 else None

    def http(self, path, method="GET", **kwargs):
        device = self.device
        device.calls.append((path, method))
        if method == "GET":
            if path == elpha.CONFIG_PATH:
                return 200, json.dumps(device.config, ensure_ascii=False).encode()
            if path == elpha.SYSTEM_PATH:
                return 200, json.dumps(device.system).encode()
            if path in device.static:
                return 200, device.static[path]
            raise AssertionError("Unreviewed Elphapex read")
        if method != "POST" or path != elpha.SETTER_PATH:
            raise AssertionError("Unreviewed Elphapex write/control")
        if kwargs.get("headers") != {"Content-Type": "application/json"}:
            raise AssertionError("The native web form requires JSON Content-Type")
        payload = deepcopy(kwargs["payload"])
        device.writes.append((path, method, payload))
        if device.apply:
            device.config = deepcopy(payload)
        if device.error:
            raise device.error
        return device.reply


class ElphapexPoolBackendTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.device = ElphaDevice(self.fixture)
        identity = record("Elphapex", "Stock", "DG1+").identity
        identity.serial = identity.mac = None
        self.record = DeviceRecord(DeviceIdentity(**vars(identity)), TelemetrySnapshot(), {})
        for key, value in self.fixture["identity"].items():
            setattr(self.record.identity, key, value)
        self.record.identity.mac = self.device.system["macaddr"].upper()
        self.record.identity.serial = "synthetic-scanner-serial"
        self.credentials = Credentials("synthetic-user", "synthetic-web-password", "basic")
        self.service = SimpleNamespace(credentials_for=lambda _: self.credentials)
        self.transport = ElphaTransport(self.device)
        self.assertEqual(elpha.STATIC_HASHES, self.fixture["static_hashes"])
        hashes = {path: hashlib.sha256(body).hexdigest() for path, body in self.device.static.items()}
        # Only fixture digest lookup changes: production hashing and the four
        # fixed paths remain real. No observed device HTML/JS is executed.
        digest_patch = patch.object(elpha, "STATIC_HASHES", hashes)
        digest_patch.start()
        self.addCleanup(digest_patch.stop)
        for target in ("requests.sessions.Session.request", "socket.create_connection"):
            guard = patch(target, side_effect=AssertionError("Actual network use is forbidden"))
            guard.start()
            self.addCleanup(guard.stop)

    def read(self):
        return backends.read_state(self.service, self.record, self.transport)

    def test_observed_schema_and_primary_route_are_exact_without_aliases_or_defaults(self):
        provenance = self.fixture["provenance"]
        self.assertTrue(provenance["read_structure_observed"])
        self.assertTrue(provenance["all_configuration_values_are_synthetic"])
        self.assertFalse(provenance["hardware_write_verified"])
        self.assertEqual(provenance["setter_requests_sent_in_observation"], 0)
        self.assertIsNone(self.record.identity.api_version)
        state = self.read()
        self.assertEqual(state.backend_id, "elphapex.web.pools")
        self.assertEqual(state.slots, self.device.config["pools"])
        self.assertEqual(state.opaque["config"], self.device.config)
        self.assertEqual(self.device.calls, [(elpha.CONFIG_PATH, "GET"), (elpha.SYSTEM_PATH, "GET")]
                         + [(path, "GET") for path in elpha.STATIC_HASHES])
        self.assertEqual(elpha.CONFIG_PATH, "/cgi-bin/get_miner_conf.cgi")
        self.assertEqual(elpha.SETTER_PATH, "/cgi-bin/set_miner_conf.cgi")
        self.assertEqual(elpha.SYSTEM_PATH, "/cgi-bin/luci/get_system_info.cgi")
        self.assertFalse(self.device.writes)

    def test_exact_full_payload_preserves_types_other_fields_and_two_slots(self):
        baseline = deepcopy(self.device.config)
        state = self.read()
        slots = deepcopy(state.slots)
        slots[0] = {"url": "stratum+tcp://synthetic-new.invalid:7777", "user": "TEST.001",
                    "pass": "  synthetic:пароль,1 ***  "}
        backends.validate_state(state, slots)
        payload = backends._write_payload(state, slots)
        self.assertEqual(payload, {**baseline, "pools": slots})
        self.assertEqual(payload["pools"][1:], baseline["pools"][1:])
        for key in baseline:
            self.assertIs(type(payload[key]), type(baseline[key]), key)
        self.assertNotIn("synthetic-web-password", repr(state))
        self.assertNotIn("synthetic-elpha-password", repr(state))
        self.assertEqual(self.device.config, baseline)
        self.assertFalse(self.device.writes)

    def test_missing_credentials_refuses_before_io(self):
        self.credentials = None
        with self.assertRaises(backends.PoolBackendError) as context:
            self.read()
        self.assertEqual(context.exception.status, "auth_required")
        self.assertFalse(self.device.calls)

    def test_model_firmware_profile_make_and_unknown_hash_refuse(self):
        baseline = deepcopy(self.record.identity)
        for field, value in (("model", "DG1"), ("firmware_version", "DG1+_SW_V1.0.5"),
                             ("profile_id", "generic.cgminer"), ("make", "Bitmain"),
                             ("firmware", "VNish")):
            with self.subTest(field=field):
                self.record.identity = deepcopy(baseline)
                setattr(self.record.identity, field, value)
                self.device.calls.clear()
                with self.assertRaises(backends.PoolBackendError):
                    self.read()
                self.assertFalse(self.device.calls)
        self.record.identity = baseline
        for path in self.device.static:
            with self.subTest(path=path):
                original = self.device.static[path]
                self.device.static[path] += b"unreviewed"
                try:
                    with self.assertRaises(backends.PoolBackendError) as context:
                        self.read()
                    self.assertTrue(hasattr(context.exception, "pool_evidence"))
                    self.assertNotIn("synthetic-elpha-password", str(context.exception))
                finally:
                    self.device.static[path] = original
        self.assertFalse(self.device.writes)

    def test_elphapex_model_prefix_and_known_mac_with_unreported_serial_are_supported(self):
        self.record.identity.model = "Elphapex DG1+"
        self.assertEqual(self.read().backend_id, elpha.BACKEND_ID)

    def test_changed_or_unverifiable_hardware_identity_refuses(self):
        original_system = deepcopy(self.device.system)
        original_mac = self.record.identity.mac
        for kind in ("mac", "missing_mac", "serial", "only_serial", "model", "version"):
            with self.subTest(kind=kind):
                self.device.system = deepcopy(original_system)
                self.record.identity.mac = original_mac
                if kind == "mac":
                    self.device.system["macaddr"] = "02:00:00:00:00:99"
                elif kind == "missing_mac":
                    self.device.system.pop("macaddr")
                elif kind == "serial":
                    self.device.system["serial"] = "synthetic-other-serial"
                elif kind == "only_serial":
                    self.record.identity.mac = None
                elif kind == "model":
                    self.device.system["minertype"] = "DG1"
                else:
                    self.device.system["system_filesystem_version"] = "DG1+_SW_V1.0.5"
                with self.assertRaises(backends.PoolBackendError) as context:
                    self.read()
                self.assertEqual(context.exception.status, "changed")
                self.assertFalse(self.device.writes)

    def test_unknown_field_missing_password_and_wrong_typed_fields_refuse(self):
        baseline = deepcopy(self.device.config)
        for kind in ("extra", "missing_pass", "extra_pool", "mode_bool", "fan_string", "missing_field"):
            with self.subTest(kind=kind):
                self.device.config = deepcopy(baseline)
                if kind == "extra":
                    self.device.config["new-field"] = "synthetic"
                elif kind == "missing_pass":
                    self.device.config["pools"][2].pop("pass")
                elif kind == "extra_pool":
                    self.device.config["pools"].append(deepcopy(self.device.config["pools"][0]))
                elif kind == "mode_bool":
                    self.device.config["fc-work-mode"] = True
                elif kind == "fan_string":
                    self.device.config["fc-fan-ctrl"] = "true"
                else:
                    self.device.config.pop("algo")
                with self.assertRaises(backends.PoolBackendError):
                    self.read()
                self.assertFalse(self.device.writes)

    def test_masked_fields_refuse_payload_without_defaults(self):
        state = self.read()
        for kind in ("slot", "nonpool"):
            with self.subTest(kind=kind):
                candidate = deepcopy(state)
                slots = deepcopy(candidate.slots)
                if kind == "slot":
                    slots[1]["pass"] = "********"
                else:
                    candidate.opaque["config"]["fc-voltage"] = "•••"
                with self.assertRaises(backends.PoolBackendError):
                    backends.validate_state(candidate, slots)
                self.assertFalse(self.device.writes)

    def test_fresh_prewrite_guards_block_changed_nonpool_or_static_or_mac(self):
        state = self.read()
        baseline, static = deepcopy(self.device.config), deepcopy(self.device.static)
        original_mac = self.device.system["macaddr"]
        for kind in ("nonpool", "static", "mac"):
            with self.subTest(kind=kind):
                self.device.config, self.device.static = deepcopy(baseline), deepcopy(static)
                self.device.system["macaddr"] = original_mac
                if kind == "nonpool":
                    self.device.config["fc-fan-pwm"] = "99"
                elif kind == "static":
                    self.device.static["/assets/Miner-f770af99.js"] += b"changed"
                else:
                    self.device.system["macaddr"] = "02:00:00:00:00:99"
                with self.assertRaises(backends.PoolBackendError):
                    backends.write_state(self.service, self.record, self.transport, state, state.slots)
                self.assertFalse(self.device.writes)

    def test_setter_ack_is_unknown_even_for_conf2_like_success_body(self):
        state = self.read()
        for reply in ((200, b"{}"), (200, b'{"stats":"success","code":"M000"}'),
                      (204, b""), (200, b"not json"), (500, b"failure"), (302, b"redirect")):
            with self.subTest(status=reply[0], body=reply[1]):
                self.device.reply = reply
                self.assertIsNone(backends.write_state(self.service, self.record, self.transport, state, state.slots))
        self.assertEqual(len(self.device.writes), 6)
        self.device.reply = (400, b"rejected")
        self.assertIs(backends.write_state(self.service, self.record, self.transport, state, state.slots), False)

    def test_timeout_or_malformed_transport_never_retries_setter(self):
        state = self.read()
        for error in (requests.ReadTimeout("synthetic-private-response"), ProtocolError("synthetic-private-response")):
            with self.subTest(error=type(error).__name__):
                self.device.writes.clear()
                self.device.error = error
                self.assertIsNone(backends.write_state(self.service, self.record, self.transport, state, state.slots))
                self.assertEqual(len(self.device.writes), 1)

    def test_evidence_has_saved_kind_and_no_pool_or_web_secrets(self):
        state = self.read()
        safe = evidence_from_state(state, record=self.record)
        self.assertEqual(safe["read_source"], "elphapex.web.conf")
        self.assertEqual(safe["reading_kind"], "saved_configuration")
        serialized = json.dumps(safe, ensure_ascii=False)
        for secret in (self.credentials.password, self.device.config["pools"][0]["pass"],
                       self.device.config["pools"][0]["url"], self.device.config["pools"][0]["user"],
                       self.device.system["macaddr"]):
            self.assertNotIn(secret, serialized)

    def test_real_core_saved_readback_confirms_full_config_with_api_acceptance_false(self):
        service = OfflineService({self.record.identity.ip: self.device})
        self.addCleanup(service.repository.close)
        service.records[self.record.identity.ip] = self.record
        service.credentials_for = self.service.credentials_for
        service.transport_factory = lambda *_: self.transport
        baseline = deepcopy(self.device.config)
        before = self.read()
        password = "  synthetic:пароль,1 ***  "
        edits = [PoolEdit(0, worker=WorkerNaming("TEST", width=3), password=password)]
        plans = list(prepare_pool_batch(service, [self.record.to_legacy()], edits))
        self.assertEqual(plans[0].status, "ready", plans[0].message)
        self.assertFalse(self.device.writes)
        result = dict(execute_pool_batch(service, plans))[self.record.identity.ip]
        self.assertEqual(result.status, "succeeded", result.message)
        self.assertFalse(result.accepted_by_api)
        expected = deepcopy(baseline)
        expected["pools"][0].update(user="TEST.001", **{"pass": password})
        self.assertEqual(self.device.config, expected)
        self.assertEqual(self.device.writes, [(elpha.SETTER_PATH, "POST", expected)])
        self.assertEqual(self.device.config["pools"][1:], baseline["pools"][1:])
        after = self.read()
        self.assertTrue(backends.equivalent_state(before, after, ignore_pools=True))
        self.assertFalse(backends.equivalent_state(before, after))
        self.assertNotIn(password, repr(result))

    def test_real_core_timeout_is_unconfirmed_and_cannot_replay(self):
        service = OfflineService({self.record.identity.ip: self.device})
        self.addCleanup(service.repository.close)
        service.records[self.record.identity.ip] = self.record
        service.credentials_for = self.service.credentials_for
        service.transport_factory = lambda *_: self.transport
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
        repeated = dict(execute_pool_batch(service, plans))[self.record.identity.ip]
        self.assertEqual(repeated, result)
        self.assertEqual(len(self.device.writes), 1)


if __name__ == "__main__":
    unittest.main()
