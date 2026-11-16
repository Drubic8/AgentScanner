"""Explicit secrets are write intent, while API mask-like values remain unknown.

These tests join real planning, payload construction, writing and verification
over offline transports. No backend/core seam is mocked; only device I/O and
reviewed static asset bytes are simulated.
"""
from contextlib import contextmanager, ExitStack
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from miner_scanner import pool_backends as backends
from miner_scanner import elphapex_pool_backend as elpha, pitbit_pool_backend as pitbit
from miner_scanner.drivers import make_record
from miner_scanner.pool_configuration import PoolEdit, WorkerNaming, prepare_pool_batch, execute_pool_batch
from miner_scanner.pool_evidence import evidence_report
from miner_scanner.profiles import ProfileRegistry
from miner_scanner.runtime import Operation
from tests.test_elphapex_pool_backend import ElphaDevice, ElphaTransport
from tests.test_pitbit_pool_backend import PitbitDevice, PitbitTransport
from tests.test_pool_integration import OfflineDevice, OfflineService, OfflineTransport


FIXTURES = Path(__file__).parent / "fixtures"
FAMILIES = ("Stock", "VNish", "Elphapex", "PitBit")


class PoolPasswordProvenanceTests(unittest.TestCase):
    def setUp(self):
        for target in ("requests.sessions.Session.request", "socket.create_connection"):
            guard = patch(target, side_effect=AssertionError("Actual network use is forbidden"))
            guard.start()
            self.addCleanup(guard.stop)
        pause = patch.object(Operation, "pause", return_value=None)
        pause.start()
        self.addCleanup(pause.stop)

    @contextmanager
    def device_case(self, family):
        ip = "192.0.2.19"
        with ExitStack() as stack:
            if family in {"Stock", "VNish"}:
                device = OfflineDevice(family)
                transport = lambda ip, op, credentials: OfflineTransport(device, ip, op)
                module = None
            else:
                module, fixture_name, device_type, transport_type = (
                    (elpha, "elphapex_dg1plus_web_pools_read.json", ElphaDevice, ElphaTransport)
                    if family == "Elphapex" else
                    (pitbit, "pitbit_c096_pools_read.json", PitbitDevice, PitbitTransport))
                fixture = json.loads((FIXTURES / fixture_name).read_text(encoding="utf-8"))
                device = device_type(fixture)
                transport = lambda *args: transport_type(device)
                hashes = {path: hashlib.sha256(body).hexdigest() for path, body in device.static.items()}
                stack.enter_context(patch.object(module, "STATIC_HASHES", hashes))
            service = OfflineService({ip: device})
            stack.callback(service.repository.close)
            service.transport_factory = transport
            if family == "Elphapex":
                identity = service.records[ip].identity
                for key, value in fixture["identity"].items():
                    setattr(identity, key, value)
                identity.mac = device.system["macaddr"]
            elif family == "PitBit":
                service.records[ip] = make_record(ProfileRegistry().by_id["bitmain.pitbit"], ip,
                    {"version": deepcopy(device.version), "system": deepcopy(device.system)})

            def slots():
                return device.config["miner"]["pools"] if family == "VNish" else device.config["pools"]

            def outgoing():
                value = device.writes[-1][2]
                payload = value["payload"] if module is None else value
                return payload["miner"]["pools"] if family == "VNish" else payload["pools"]

            yield SimpleNamespace(family=family, ip=ip, service=service, device=device, slots=slots,
                                  outgoing=outgoing, accepted=family != "Elphapex")

    def prepare(self, case, edits):
        row = case.service.records[case.ip].to_legacy()
        return list(prepare_pool_batch(case.service, [row], edits))[0]

    def execute(self, case, plan):
        return dict(execute_pool_batch(case.service, [plan]))[case.ip]

    def test_explicit_all_star_or_bullet_password_is_sent_once_and_mask_readback_is_unconfirmed(self):
        for family in FAMILIES:
            for password in ("*", "***", "•••", "*•*"):
                with self.subTest(family=family, password_kind="stars" if password == "***" else "bullets"):
                    with self.device_case(family) as case:
                        baseline = deepcopy(case.device.config)
                        plan = self.prepare(case, [PoolEdit(0, password=password)])
                        self.assertEqual(plan.status, "ready", plan.message)
                        self.assertEqual(plan._prepared.explicit_password_slots, (0,))
                        self.assertFalse(case.device.writes)
                        result = self.execute(case, plan)
                        self.assertEqual(result.status, "unconfirmed", result.message)
                        self.assertEqual(result.accepted_by_api, case.accepted)
                        self.assertEqual(case.outgoing()[0]["pass"], password)
                        self.assertEqual(len(case.device.writes), 1)
                        expected = deepcopy(baseline)
                        expected_slots = expected["miner"]["pools"] if family == "VNish" else expected["pools"]
                        expected_slots[0]["pass"] = password
                        self.assertEqual(case.device.config, expected)
                        report = json.dumps(evidence_report([plan]), ensure_ascii=False)
                        self.assertNotIn("explicit_password_slots", report)
                        self.assertNotIn("explicit_password_slots", repr(plan))
                        self.assertNotIn(password, report)
                        self.assertEqual(self.execute(case, plan), result)
                        self.assertEqual(len(case.device.writes), 1)

    def test_identical_read_mask_and_explicit_password_still_request_a_real_change(self):
        for family in FAMILIES:
            for password in ("*", "***", "•••"):
                with self.subTest(family=family):
                    with self.device_case(family) as case:
                        case.slots()[0]["pass"] = password
                        plan = self.prepare(case, [PoolEdit(0, password=password)])
                        self.assertEqual(plan.status, "ready", plan.message)
                        self.assertEqual([(c.field, c.before, c.after) for c in plan.changes],
                                         [("pass", "<hidden>", "<changed>")])
                        result = self.execute(case, plan)
                        self.assertEqual(result.status, "unconfirmed", result.message)
                        self.assertEqual(case.outgoing()[0]["pass"], password)
                        self.assertEqual(len(case.device.writes), 1)

    def test_explicit_cleartext_replaces_a_read_mask_and_can_be_confirmed(self):
        for family in FAMILIES:
            with self.subTest(family=family):
                with self.device_case(family) as case:
                    case.slots()[0]["pass"] = "***"
                    plan = self.prepare(case, [PoolEdit(0, password="synthetic-real-replacement")])
                    self.assertEqual(plan.status, "ready", plan.message)
                    result = self.execute(case, plan)
                    self.assertEqual(result.status, "succeeded", result.message)
                    self.assertEqual(case.outgoing()[0]["pass"], "synthetic-real-replacement")
                    self.assertEqual(len(case.device.writes), 1)

    def test_explicit_password_does_not_authorize_mask_in_untouched_slot_or_worker(self):
        for family in FAMILIES:
            for field, position in (("pass", 2), ("user", 0), ("url", 0)):
                with self.subTest(family=family, field=field):
                    with self.device_case(family) as case:
                        case.slots()[position][field] = "***"
                        plan = self.prepare(case, [PoolEdit(0, password="***")])
                        self.assertEqual(plan.status, "unsupported", plan.message)
                        self.assertIsNone(plan._prepared)
                        self.assertFalse(case.device.writes)

    def test_mask_without_any_explicit_replacement_still_refuses(self):
        for family in FAMILIES:
            with self.subTest(family=family):
                with self.device_case(family) as case:
                    case.slots()[0]["pass"] = "***"
                    plan = self.prepare(case, [PoolEdit(0, worker=WorkerNaming("TEST"))])
                    self.assertEqual(plan.status, "unsupported", plan.message)
                    self.assertFalse(case.device.writes)

    def test_sealed_provenance_mutation_is_rejected_before_any_setter(self):
        for family in FAMILIES:
            for mutated in ((), (1,), (0, 1)):
                with self.subTest(family=family, mutated=mutated):
                    with self.device_case(family) as case:
                        plan = self.prepare(case, [PoolEdit(0, password="***")])
                        self.assertEqual(plan.status, "ready", plan.message)
                        changed = replace(plan, _prepared=replace(plan._prepared, explicit_password_slots=mutated))
                        result = self.execute(case, changed)
                        self.assertEqual(result.status, "skipped", result.message)
                        self.assertFalse(case.device.writes)

    def test_fresh_configuration_signature_is_not_relaxed_by_write_intent(self):
        for family in FAMILIES:
            with self.subTest(family=family):
                with self.device_case(family) as case:
                    plan = self.prepare(case, [PoolEdit(0, password="*")])
                    self.assertEqual(plan.status, "ready", plan.message)
                    case.slots()[0]["pass"] = "•••"
                    result = self.execute(case, plan)
                    self.assertEqual(result.status, "skipped", result.message)
                    self.assertFalse(case.device.writes)

    def test_nonpool_full_config_and_hotel_masks_remain_blocked(self):
        for family, key in (("Stock", "bitmain-fan-pwm"), ("Elphapex", "fc-voltage"),
                            ("PitBit", "poolshotelfee")):
            with self.subTest(family=family):
                with self.device_case(family) as case:
                    if family == "PitBit":
                        case.device.config[key][0]["pass"] = "***"
                    else:
                        case.device.config[key] = "***"
                    plan = self.prepare(case, [PoolEdit(0, password="*")])
                    self.assertEqual(plan.status, "unsupported", plan.message)
                    self.assertFalse(case.device.writes)

    def test_backend_direct_calls_are_fail_closed_without_canonical_explicit_provenance(self):
        for family in FAMILIES:
            with self.subTest(family=family):
                with self.device_case(family) as case:
                    record = case.service.records[case.ip]
                    with case.service.transport_factory(case.ip, Operation(), case.service.credentials_for(case.ip)) as transport:
                        state = backends.read_state(case.service, record, transport)
                    slots = deepcopy(state.slots)
                    slots[0]["pass"] = "***"
                    with self.assertRaises(backends.PoolBackendError):
                        backends.validate_state(state, slots)
                    for invalid in ([0], (True,), (0, 0), (3,), (1, 0)):
                        with self.subTest(invalid=invalid), self.assertRaises(backends.PoolBackendError):
                            backends.validate_state(state, slots, explicit_password_slots=invalid)
                    backends.validate_state(state, slots, explicit_password_slots=(0,))
                    payload = backends._write_payload(state, slots, explicit_password_slots=(0,))
                    sent_slots = payload["miner"]["pools"] if family == "VNish" else payload["pools"]
                    self.assertEqual(sent_slots[0]["pass"], "***")
                    self.assertFalse(case.device.writes)


if __name__ == "__main__":
    unittest.main()
