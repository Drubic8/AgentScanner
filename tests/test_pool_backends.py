"""Pool ABI checks against synthetic stored configurations; never use a network."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import requests

from miner_scanner.models import Credentials
from miner_scanner import pool_backends as backend
from miner_scanner import stock_compatibility as stock
from miner_scanner.runtime import AuthenticationError


def record(make="Bitmain", firmware="Stock", model="Antminer T21"):
    return SimpleNamespace(identity=SimpleNamespace(
        ip="192.0.2.1", make=make, firmware=firmware, model=model,
        firmware_version="example-build", api_version="3.1", device_id="fixture-device",
        profile_id="bitmain.vnish" if firmware.casefold() == "vnish" else "bitmain.stock",
        profile_version=1, fingerprint="fixture-passport"))


def service(credentials=Credentials("root", "fixture-access-password")):
    return SimpleNamespace(credentials_for=lambda ip: credentials,
                           credential_candidates=lambda ip, family: [credentials] if credentials else [],
                           remember_credentials=lambda ip, credentials: None)


def pools():
    return [{"url": "stratum+tcp://primary.example.invalid:3333", "user": "OWNER.old1", "pass": "pool-secret1"},
            {"url": "", "user": "", "pass": ""},
            {"url": "stratum+tcp://backup.example.invalid:4444", "user": "BACKUP.old3", "pass": "pool-secret3"}]


def stock_config():
    return {"pools": pools(), "bitmain-work-mode": "2", "bitmain-fan-ctrl": True,
            "bitmain-fan-pwm": "75", "bitmain-hashrate-percent": "90"}


def settings_spec():
    """Minimal sanitized form of docs/vnish_openapi.json /settings schemas."""
    pool = {"type": "object", "required": ["url", "user", "pass"],
            "properties": {key: {"type": "string"} for key in ("url", "user", "pass")}}
    miner = {"type": "object", "properties": {
        "pools": {"type": ["array", "null"], "items": False, "prefixItems": [deepcopy(pool) for _ in range(3)]},
        "cooling": {"type": "null"}, "overclock": {"type": "null"}, "misc": {"type": "null"}}}
    input_config = {"type": "object", "properties": {"miner": {"$ref": "#/components/schemas/Miner"}}}
    result = {"type": "object", "required": ["restart_required", "reboot_required"],
              "properties": {key: {"type": "boolean"} for key in ("restart_required", "reboot_required")}}
    spec = {"openapi": "3.1.0", "servers": [{"url": "/api/v1"}], "paths": {},
            "components": {"schemas": {"Input": input_config, "View": deepcopy(input_config),
                                       "Miner": miner, "Saved": result}}}
    response = lambda name: {"200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/" + name}}}}}
    spec["paths"]["/settings"] = {"get": {"responses": response("View")}, "post": {
        "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Input"}}}},
        "responses": response("Saved")}}
    return spec


class Transport:
    def __init__(self, config=None, spec=None):
        self.config = deepcopy(config or stock_config())
        self.spec = deepcopy(spec)
        self.reads, self.writes = [], []
        self.reply = (200, b'{"stats":"success","code":"M000"}')
        self.write_error = None

    def http_json(self, path, method="GET", **kwargs):
        self.reads.append((path, method, deepcopy(kwargs)))
        if path == "/cgi-bin/get_miner_conf.cgi":
            return deepcopy(self.config)
        if path == "/docs/api-doc.json":
            return deepcopy(self.spec)
        if path == "/api/v1/unlock":
            return {"token": "fixture-ephemeral-token"}
        if path == "/api/v1/settings":
            return deepcopy(self.config)
        raise AssertionError("Unreviewed API path requested")

    def http(self, path, method="GET", **kwargs):
        if method != "POST":
            raise AssertionError("Unexpected non-write request")
        self.writes.append((path, method, deepcopy(kwargs)))
        if self.write_error:
            raise self.write_error
        return self.reply


class StockPoolBackendTests(unittest.TestCase):
    def setUp(self):
        self.record = record()
        self.transport = Transport()
        self.service = service()
        self.definition_id, self.definition = next((key, value) for key, value in stock.interfaces()["miner"].items()
                                                  if value["source_asset"] == "miner.b91570.js")
        self.probe = patch.object(stock, "probe", return_value={"miner": self.definition_id})
        self.probe.start()
        self.addCleanup(self.probe.stop)

    def read(self):
        return backend.read_state(self.service, self.record, self.transport)

    def test_pool_write_preserves_power_fan_parameters_and_unselected_slots(self):
        state = self.read()
        changed = deepcopy(state.slots)
        changed[0]["user"] = "TEST.001"
        self.assertTrue(backend.write_state(self.service, self.record, self.transport, state, changed))
        path, method, arguments = self.transport.writes[0]
        self.assertEqual((path, method), ("/cgi-bin/set_miner_conf.cgi", "POST"))
        self.assertEqual(arguments["payload"], {"pools": changed, "bitmain-fan-ctrl": True,
            "bitmain-fan-pwm": "75", "bitmain-hashrate-percent": "90", "miner-mode": 2})
        self.assertEqual(changed[1:], pools()[1:])
        self.assertEqual(self.transport.config, stock_config())
        self.assertEqual(state.slots[0]["user"], "OWNER.old1")

    def test_unfamiliar_build_uses_exact_read_schema_without_firmware_date_guess(self):
        self.probe.stop()
        with patch.object(stock, "probe", return_value={"miner": "unreviewed-ui"}):
            self.record.identity.firmware_version = "future-build"
            self.assertEqual(self.read().backend_id, "bitmain.stock.cgi")
        self.assertFalse(self.transport.writes)

    def test_unknown_configuration_field_prevents_full_write(self):
        self.transport.config["future-voltage-control"] = "do-not-drop"
        with self.assertRaises(backend.PoolBackendError):
            self.read()
        self.assertFalse(self.transport.writes)

    def test_ambiguous_read_to_write_mappings_are_not_chosen_by_trial(self):
        self.probe.stop()
        catalog = deepcopy(stock.interfaces())
        duplicate = deepcopy(self.definition)
        duplicate["write_mode"] = "another-mode-key"
        catalog["miner"]["synthetic-conflicting-schema"] = duplicate
        with patch.object(stock, "interfaces", return_value=catalog), patch.object(stock, "probe", return_value={}):
            with self.assertRaises(backend.PoolBackendError):
                self.read()
        self.assertFalse(self.transport.writes)

    def test_masked_password_is_not_forwarded_but_explicit_replacement_is_allowed(self):
        self.transport.config["pools"][0]["pass"] = "********"
        state = self.read()
        with self.assertRaises(backend.PoolBackendError):
            backend.write_state(self.service, self.record, self.transport, state, state.slots)
        changed = deepcopy(state.slots)
        changed[0]["pass"] = "replacement-secret"
        self.assertTrue(backend.write_state(self.service, self.record, self.transport, state, changed))
        self.assertNotIn("********", json.dumps(self.transport.writes))

    def test_missing_password_is_retained_as_unknown_in_read_snapshot(self):
        self.transport.config["pools"][2].pop("pass")
        self.assertNotIn("pass", self.read().slots[2])

    def test_no_io_validation_blocks_masked_unrelated_settings_before_dispatch(self):
        for mask in ("*", "**", "********", "•••"):
            with self.subTest(mask=mask):
                self.transport.config["bitmain-fan-pwm"] = mask
                state = self.read()
                changed = deepcopy(state.slots)
                changed[0]["user"] = "TEST.001"
                reads = len(self.transport.reads)
                with self.assertRaises(backend.PoolBackendError):
                    backend.validate_state(state, changed)
                self.assertEqual(len(self.transport.reads), reads)
                self.assertFalse(self.transport.writes)

    def test_no_io_validation_blocks_unknown_enabled_password(self):
        self.transport.config["pools"][0].pop("pass")
        state = self.read()
        with self.assertRaises(backend.PoolBackendError):
            backend.validate_state(state, state.slots)
        changed = deepcopy(state.slots)
        changed[0]["pass"] = "replacement-secret"
        self.assertIsNone(backend.validate_state(state, changed))
        self.assertFalse(self.transport.writes)

    def test_snapshot_identity_changes_block_write(self):
        state = self.read()
        self.record.identity.fingerprint = "different-device"
        with self.assertRaises(backend.PoolBackendError) as problem:
            backend.write_state(self.service, self.record, self.transport, state, state.slots)
        self.assertEqual(problem.exception.status, "changed")
        self.assertFalse(self.transport.writes)

    def test_prepared_backend_cannot_bypass_family_route(self):
        state = self.read()
        self.record.identity.make = "unreviewed-maker"
        state.opaque["identity"] = backend._identity_key(self.record)
        with self.assertRaises(backend.PoolBackendError):
            backend.write_state(self.service, self.record, self.transport, state, state.slots)
        self.assertFalse(self.transport.writes)

    def test_timeout_or_5xx_is_uncertain_and_not_retried(self):
        state = self.read()
        for error, reply in ((requests.ReadTimeout("private-error-data"), (200, b"{}")),
                             (None, (500, b"private-error-data")), (None, (200, b"not-json"))):
            with self.subTest(error=type(error).__name__):
                self.transport.writes.clear()
                self.transport.write_error, self.transport.reply = error, reply
                self.assertIsNone(backend.write_state(self.service, self.record, self.transport, state, state.slots))
                self.assertEqual(len(self.transport.writes), 1)

    def test_explicit_rejection_is_false(self):
        state = self.read()
        self.transport.reply = (200, b'{"stats":"error","code":"M001"}')
        self.assertIs(backend.write_state(self.service, self.record, self.transport, state, state.slots), False)
        self.assertEqual(len(self.transport.writes), 1)

    def test_unrecognized_http_200_reply_is_not_an_api_acceptance(self):
        state = self.read()
        for reply in (b'{}', b'{"unrelated":"value"}', b'{"code":0.0}', b'{"code":false}',
                      b'{"stats":"success"}', b'{"success":true}'):
            with self.subTest(reply=reply):
                self.transport.reply = (200, reply)
                self.assertIsNone(backend.write_state(self.service, self.record, self.transport, state, state.slots))

    def test_missing_credentials_block_before_reading_configuration(self):
        with self.assertRaises(backend.PoolBackendError) as problem:
            backend.read_state(service(None), self.record, self.transport)
        self.assertEqual(problem.exception.status, "auth_required")
        self.assertFalse(self.transport.reads)

    def test_secrets_are_excluded_from_representations_and_signatures_are_sensitive(self):
        state = self.read()
        self.assertNotIn("pool-secret", repr(state))
        changed = deepcopy(state)
        changed.opaque["config"]["pools"][0]["pass"] = "different-secret"
        self.assertNotEqual(backend.state_signature(state), backend.state_signature(changed))
        self.assertTrue(backend.equivalent_state(state, changed, ignore_pools=True))
        changed.opaque["config"]["bitmain-fan-pwm"] = "100"
        self.assertFalse(backend.equivalent_state(state, changed, ignore_pools=True))

    def test_ip_suffix_flag_is_exposed_without_silently_changing_it(self):
        self.transport.config["bitmain-user-ip-cat"] = True
        state = self.read()
        self.assertTrue(state.opaque["worker_auto_suffix"])
        self.assertTrue(backend.write_state(self.service, self.record, self.transport, state, state.slots))
        self.assertIs(self.transport.writes[0][2]["payload"]["bitmain-user-ip-cat"], True)


class VnishPoolBackendTests(unittest.TestCase):
    def setUp(self):
        self.record = record(firmware="VNish", model="Antminer L9")
        self.service = service()
        self.config = {"miner": {"pools": pools(), "cooling": {"target_temp": 70},
                                  "misc": {"some_existing_setting": True}},
                       "network": {"hostname": "fixture-miner"}, "ui": {"theme": "dark"}}
        self.transport = Transport(self.config, settings_spec())
        self.transport.reply = (200, b'{"restart_required":true,"reboot_required":false}')

    def read(self):
        return backend.read_state(self.service, self.record, self.transport)

    def test_minimal_settings_payload_keeps_nonpool_configuration_out_of_write(self):
        state = self.read()
        changed = deepcopy(state.slots)
        changed[2]["url"] = "stratum+tcp://new.example.invalid:3333"
        self.assertTrue(backend.write_state(self.service, self.record, self.transport, state, changed))
        self.assertEqual(self.transport.writes, [("/api/v1/settings", "POST", {
            "payload": {"miner": {"pools": changed}}, "headers": {
                "Authorization": "Bearer fixture-ephemeral-token", "Cache-Control": "no-cache"}})])
        self.assertIs(state.opaque["restart_required"], True)
        self.assertIs(state.opaque["reboot_required"], False)
        self.assertEqual(self.transport.config, self.config)

    def test_current_primary_reference_schema_is_accepted(self):
        source = Path(__file__).resolve().parents[1] / "docs" / "vnish_openapi.json"
        self.assertEqual(backend._vnish_contract(json.loads(source.read_text(encoding="utf-8"))),
                         {"path": "/api/v1/settings", "payload": "miner.pools"})

    def test_changed_required_field_remote_reference_or_oversize_pool_list_blocks(self):
        for change in ("required", "external", "pool_fields"):
            with self.subTest(change=change):
                self.transport.spec = settings_spec()
                schemas = self.transport.spec["components"]["schemas"]
                if change == "required":
                    schemas["Input"]["required"] = ["network"]
                elif change == "external":
                    schemas["Input"]["properties"]["miner"] = {"$ref": "https://untrusted.example.invalid/schema"}
                else:
                    schemas["Miner"]["properties"]["pools"]["prefixItems"][0]["required"].append("new_secret")
                with self.assertRaises(backend.PoolBackendError):
                    self.read()
                self.assertFalse(self.transport.writes)
                self.assertFalse(any(path == "/api/v1/unlock" for path, _, _ in self.transport.reads))

    def test_unknown_settings_operation_or_spec_is_not_replaced_by_firmware_guess(self):
        del self.transport.spec["paths"]["/settings"]["post"]
        with self.assertRaises(backend.PoolBackendError):
            self.read()
        self.assertFalse(self.transport.writes)

    def test_slot_index_order_and_empty_middle_slot_preserved(self):
        state = self.read()
        self.assertEqual(state.slots, pools())
        self.assertEqual(state.slots[1], {"url": "", "user": "", "pass": ""})

    def test_missing_password_remains_unknown_until_explicit_replacement(self):
        self.transport.config["miner"]["pools"][0].pop("pass")
        state = self.read()
        self.assertNotIn("pass", state.slots[0])
        with self.assertRaises(backend.PoolBackendError):
            backend.write_state(self.service, self.record, self.transport, state, state.slots)
        self.assertFalse(self.transport.writes)

    def test_empty_slot_with_missing_password_requires_explicit_value_before_readiness(self):
        self.transport.config["miner"]["pools"][1].pop("pass")
        state = self.read()
        with self.assertRaises(backend.PoolBackendError):
            backend.validate_state(state, state.slots)
        changed = deepcopy(state.slots)
        changed[1]["pass"] = ""
        self.assertIsNone(backend.validate_state(state, changed))
        self.assertFalse(self.transport.writes)

    def test_service_pool_objects_or_missing_addresses_are_not_treated_as_config(self):
        for slot in ({"user": "worker", "pass": "x"},
                     {"id": 3, "pool_type": "DevFee", "url": "service.invalid", "user": "fee", "pass": "x"}):
            with self.subTest(slot_keys=list(slot)):
                self.transport.config["miner"]["pools"] = [slot]
                with self.assertRaises(backend.PoolBackendError):
                    self.read()
                self.assertFalse(self.transport.writes)

    def test_token_rotation_does_not_change_configuration_signature(self):
        state = self.read()
        rotated = deepcopy(state)
        rotated.opaque["headers"]["Authorization"] = "Bearer another-token"
        rotated.opaque["restart_required"] = True
        self.assertEqual(backend.state_signature(state), backend.state_signature(rotated))
        self.assertTrue(backend.equivalent_state(state, rotated))
        self.assertNotIn("fixture-ephemeral-token", repr(state))

    def test_saved_settings_without_valid_flags_remain_uncertain(self):
        state = self.read()
        self.transport.reply = (200, b'{"restart_required":"true","reboot_required":false}')
        self.assertIsNone(backend.write_state(self.service, self.record, self.transport, state, state.slots))
        self.assertEqual(len(self.transport.writes), 1)

    def test_authentication_failure_uses_fixed_public_message(self):
        original = self.transport.http_json
        def fail(path, *args, **kwargs):
            if path == "/api/v1/unlock":
                raise AuthenticationError("raw-private-reply")
            return original(path, *args, **kwargs)
        self.transport.http_json = fail
        with self.assertRaises(backend.PoolBackendError) as problem:
            self.read()
        self.assertEqual(problem.exception.status, "auth_required")
        self.assertNotIn("raw-private-reply", str(problem.exception))
        self.assertFalse(self.transport.writes)


if __name__ == "__main__":
    unittest.main()
