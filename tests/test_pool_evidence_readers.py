"""Read-only support collection for blocked writer families; no real devices."""
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import requests

from miner_scanner.models import DeviceIdentity, DeviceRecord, TelemetrySnapshot, Credentials
from miner_scanner.pool_backends import PoolBackendError
from miner_scanner.pool_configuration import collect_pool_evidence_batch
from miner_scanner.pool_evidence import evidence_report
from miner_scanner.pool_evidence_readers import read_evidence
from miner_scanner.profiles import ProfileRegistry
from miner_scanner.runtime import AuthenticationError, Cancelled, Operation
from tests.test_pool_integration import OfflineDevice, OfflineService


PRIVATE = "PRIVATE-POOL-READER-SECRET-4499"
URL = "stratum+tcp://private.example.invalid:3333"
WORKER = "PRIVATE-WORKER.77"
TOKEN = "PRIVATE-SESSION-TOKEN-7788"
SALT = "PRIVATE-SALT-9988"


def pools():
    return [{"url": URL, "user": WORKER, "pass": PRIVATE},
            {"url": "", "user": "", "pass": ""},
            {"url": "backup.invalid:4444", "user": "BACKUP.77", "pass": PRIVATE}]


def make_record(registry, profile_id):
    profile = registry.by_id[profile_id]
    model = (profile.match or {}).get("models", ["Antminer S21"])[0]
    if profile_id == "microbt.rpc3":
        model = "M61_VL50"
    identity = DeviceIdentity(device_id="PRIVATE-DEVICE-55", ip="192.0.2.55", make=profile.make,
        model=model, firmware=profile.firmware, firmware_version="fixture-build",
        api_version="3.0.5" if profile_id == "microbt.rpc3" else "3.1", serial="PRIVATE-SERIAL-55",
        mac="02:00:00:00:00:55", profile_id=profile.id, profile_version=profile.version,
        fingerprint="PRIVATE-FINGERPRINT-55")
    return DeviceRecord(identity, TelemetrySnapshot(), {})


class ReadTransport:
    def __init__(self):
        self.calls = []
        self.data = {
            ("GET", "/cgi-bin/get_miner_conf.cgi"): {"pools": pools(), "bitmain-work-mode": "0", PRIVATE: PRIVATE},
            ("GET", "/cgi-bin/luci/get_miner_conf.cgi"): {"pools": pools(), "fc-work-mode": "0", "fc_fan_ctrl": True},
            ("GET", "/docs/api-doc.json"): {"openapi": "3.1.0", "servers": [{"url": "/api/v1"}],
                "paths": {"/settings": {"get": {"responses": {"200": {"description": PRIVATE}}}}}},
            ("POST", "/api/v1/unlock"): {"token": TOKEN},
            ("GET", "/api/v1/settings"): {"miner": {"pools": pools(), "cooling": {"target_temp": 70}},
                "network": {"ip": "192.0.2.55", "hostname": PRIVATE}},
            ("POST", "/cgi-bin/minerStatus.cgi"): {"summary": {"miner": "X16-Q"}, "pools": {"pool": pools()}},
            ("GET", "/mcb/pools"): pools(),
            ("rpc", "get.device.info", None): {"code": 0, "desc": "get.device.info", "msg": {
                "salt": SALT, "miner": {"working": "true", "type": "M61_VL50"},
                "system": {"api": "3.0.5", "fwversion": "fixture-build", "apiswitch": "0"}}},
            ("rpc", "get.miner.status", "pools"): {"code": 0, "desc": "get.miner.status", "msg": {
                "pools": [{"id": 1, "url": URL, "account": WORKER, "stratum-active": True}]}},
            ("cgminer", "pools"): {"STATUS": [{"STATUS": "S", "Msg": "ok"}], "POOLS": [
                {"POOL": 2, "URL": URL, "User": WORKER}, {"POOL": 0, "URL": "", "User": ""}]},
        }

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def _reply(self, key, kwargs):
        self.calls.append((key, deepcopy(kwargs)))
        if key not in self.data:
            raise AssertionError("Attempted an unreviewed operation")
        value = self.data[key]
        if isinstance(value, Exception):
            raise value
        return deepcopy(value)

    def http_json(self, path, method="GET", **kwargs):
        return self._reply((method, path), kwargs)

    def rpc(self, command, parameter=None):
        if not command.startswith("get."):
            raise AssertionError("Attempted an RPC write")
        return self._reply(("rpc", command, parameter), {})

    def cgminer(self, command):
        if command != "pools":
            raise AssertionError("Attempted a CGMiner write")
        return self._reply(("cgminer", command), {})


class PoolEvidenceReaderTests(unittest.TestCase):
    def setUp(self):
        self.registry = ProfileRegistry()
        self.credential_reads = []
        def credentials(ip):
            self.credential_reads.append(ip)
            return Credentials("root", PRIVATE)
        self.service = SimpleNamespace(registry=self.registry, credentials_for=credentials,
            credential_candidates=lambda ip, family: [Credentials("root", PRIVATE)],
            remember_credentials=lambda ip, credentials: None)
        self.transport = ReadTransport()

    def read(self, profile_id):
        return read_evidence(self.service, make_record(self.registry, profile_id), self.transport)

    def assert_private_absent(self, result):
        serialized = json.dumps(result, ensure_ascii=False)
        for private in (PRIVATE, URL, WORKER, TOKEN, SALT, "PRIVATE-DEVICE-55", "PRIVATE-SERIAL-55",
                        "PRIVATE-FINGERPRINT-55", "192.0.2.55", "02:00:00:00:00:55"):
            self.assertNotIn(private, serialized)

    def test_every_catalog_profile_has_an_explicit_read_only_route_or_refusal(self):
        seen = set()
        for profile in self.registry.profiles:
            with self.subTest(profile=profile.id):
                self.transport.calls.clear()
                if profile.id == "generic.cgminer_web":
                    with self.assertRaises(PoolBackendError):
                        self.read(profile.id)
                    self.assertFalse(self.transport.calls)
                else:
                    result = self.read(profile.id)
                    self.assert_private_absent(result)
                    self.assertTrue(self.transport.calls)
                    self.assertEqual(result["identity"]["profile_id"], profile.id)
                    self.assertIn("configuration_schema", result)
                    self.assertEqual(len(result["pool_slots"]), 1 if profile.id == "microbt.rpc3" else
                                     2 if profile.parser in {"avalon", "ipollo", "generic"} else 3)
                seen.add(profile.id)
        self.assertEqual(seen, set(self.registry.by_id))

    def test_fixed_http_routes_and_semantics_do_not_require_writer_compatibility(self):
        cases = [
            ("bitmain.stock.s19.20221206", "stock.cgi", "saved_configuration", ("GET", "/cgi-bin/get_miner_conf.cgi")),
            ("bitmain.pitbit", "antminer.cgi", "saved_configuration", ("GET", "/cgi-bin/get_miner_conf.cgi")),
            ("elphapex.luci", "elphapex.luci", "unknown", ("GET", "/cgi-bin/luci/get_miner_conf.cgi")),
            ("jasminer.web", "jasminer.status", "runtime", ("POST", "/cgi-bin/minerStatus.cgi")),
            ("goldshell.mcb", "goldshell.pools", "unknown", ("GET", "/mcb/pools")),
        ]
        for profile, source, kind, request in cases:
            with self.subTest(profile=profile):
                self.transport.calls.clear()
                result = self.read(profile)
                self.assertEqual(result["read_source"], source)
                self.assertEqual(result["reading_kind"], kind)
                self.assertEqual([key for key, _ in self.transport.calls], [request])
                self.assert_private_absent(result)
        self.assertFalse(self.credential_reads)
        self.assertEqual(self.transport.calls[0][1], {"expect_list": True})

    def test_runtime_cgminer_array_position_is_not_reinterpreted_as_saved_slot_id(self):
        for profile in ("canaan.avalon", "ipollo.cgminer", "generic.cgminer"):
            with self.subTest(profile=profile):
                self.transport.calls.clear()
                result = self.read(profile)
                self.assertEqual(result["reading_kind"], "runtime")
                self.assertEqual(result["read_source"], "cgminer.pools")
                self.assertEqual(result["pool_slots"][0]["position"], 1)
                self.assertEqual(result["pool_slots"][0]["fields"]["user"], "present")
                self.assertEqual(self.transport.calls, [(("cgminer", "pools"), {})])

    def test_rpc_read_works_with_write_disabled_without_credentials_or_tokens(self):
        result = self.read("microbt.rpc3")
        self.assertEqual(result["reading_kind"], "runtime")
        self.assertEqual(result["read_source"], "whatsminer.rpc_pools")
        self.assertEqual([key for key, _ in self.transport.calls], [
            ("rpc", "get.device.info", None), ("rpc", "get.miner.status", "pools")])
        self.assertFalse(self.credential_reads)
        self.assert_private_absent(result)

    def test_rpc_legacy_api_or_unknown_profile_never_tries_port4433_commands(self):
        current = make_record(self.registry, "microbt.rpc3")
        for api in (None, "2.0.3", "3.1.0", "future"):
            with self.subTest(api=api):
                current.identity.api_version = api
                with self.assertRaises(PoolBackendError):
                    read_evidence(self.service, current, self.transport)
                self.assertFalse(self.transport.calls)

    def test_rpc_fresh_info_identity_changes_or_unknown_shape_stop_before_pool_read(self):
        for change in ("model", "firmware", "api", "bad_info"):
            with self.subTest(change=change):
                self.transport = ReadTransport()
                info = self.transport.data[("rpc", "get.device.info", None)]
                if change == "model":
                    info["msg"]["miner"]["type"] = "different-device"
                elif change == "firmware":
                    info["msg"]["system"]["fwversion"] = "new-build"
                elif change == "api":
                    info["msg"]["system"]["api"] = "3.0.4"
                else:
                    info["code"] = -1
                with self.assertRaises(PoolBackendError):
                    self.read("microbt.rpc3")
                self.assertEqual(len(self.transport.calls), 1)

    def test_rpc_read_failure_is_not_published_as_successful_observation(self):
        for code in (-1, -4, False, 0.0):
            with self.subTest(code=code):
                self.transport.data[("rpc", "get.miner.status", "pools")] = {
                    "code": code, "desc": "get.miner.status", "msg": PRIVATE}
                with self.assertRaises(PoolBackendError) as problem:
                    self.read("microbt.rpc3")
                self.assertNotIn(PRIVATE, str(problem.exception))
                self.assertEqual(problem.exception.status, "auth_required" if code == -4 else "unsupported")

    def test_rpc_salt_is_not_exported_when_echoed_in_firmware_identity(self):
        current = make_record(self.registry, "microbt.rpc3")
        current.identity.firmware_version = SALT
        self.transport.data[("rpc", "get.device.info", None)]["msg"]["system"]["fwversion"] = SALT
        self.assert_private_absent(read_evidence(self.service, current, self.transport))

    def test_vnish_read_declaration_and_unlock_are_sufficient_without_post_schema(self):
        result = self.read("bitmain.vnish")
        self.assertEqual(result["reading_kind"], "saved_configuration")
        self.assertEqual(result["api"], {"settings_get": True, "settings_post": False})
        self.assertEqual([key for key, _ in self.transport.calls], [
            ("GET", "/docs/api-doc.json"), ("POST", "/api/v1/unlock"), ("GET", "/api/v1/settings")])
        self.assertEqual(self.transport.calls[1][1], {"payload": {"pw": PRIVATE}})
        self.assertEqual(self.transport.calls[2][1]["headers"]["Authorization"], "Bearer " + TOKEN)
        self.assert_private_absent(result)

    def test_vnish_unknown_local_declaration_never_unlocks_or_follows_server_urls(self):
        for change in ("missing_get", "external", "required_param", "required_body", "operation_servers"):
            with self.subTest(change=change):
                self.transport = ReadTransport()
                spec = self.transport.data[("GET", "/docs/api-doc.json")]
                operation = spec["paths"]["/settings"]["get"]
                if change == "missing_get":
                    spec["paths"]["/settings"].pop("get")
                elif change == "external":
                    spec["servers"] = [{"url": URL}]
                elif change == "required_param":
                    operation["parameters"] = [{"name": "private", "required": True}]
                elif change == "required_body":
                    operation["requestBody"] = {"required": True}
                else:
                    operation["servers"] = URL
                with self.assertRaises(PoolBackendError) as problem:
                    self.read("bitmain.vnish")
                self.assertEqual([key for key, _ in self.transport.calls], [("GET", "/docs/api-doc.json")])
                self.assert_private_absent(problem.exception.pool_evidence)

    def test_vnish_actual_password_username_and_bare_token_never_leave_success_evidence(self):
        current = make_record(self.registry, "bitmain.vnish")
        current.identity.model = "Antminer " + PRIVATE
        current.identity.firmware_version = TOKEN
        current.identity.api_version = "root"
        result = read_evidence(self.service, current, self.transport)
        self.assert_private_absent(result)
        self.assertNotEqual(result["identity"]["api_version"], "root")
        self.assertNotIn(PRIVATE, json.dumps(evidence_report([result])))

    def test_vnish_actual_secrets_are_redacted_from_format_and_auth_error_evidence(self):
        for response in ({}, AuthenticationError(PRIVATE)):
            with self.subTest(error_type=type(response).__name__):
                self.transport = ReadTransport()
                self.transport.data[("GET", "/api/v1/settings")] = response
                current = make_record(self.registry, "bitmain.vnish")
                current.identity.model = "Antminer " + PRIVATE
                current.identity.firmware_version = TOKEN
                current.identity.api_version = "root"
                with self.assertRaises(PoolBackendError) as problem:
                    read_evidence(self.service, current, self.transport)
                self.assert_private_absent(problem.exception.pool_evidence)
                self.assertNotEqual(problem.exception.pool_evidence["identity"]["api_version"], "root")

    def test_vnish_successfully_authenticated_candidate_is_used_for_redaction(self):
        authenticated = Credentials("fixture-success-user", "PRIVATE-SUCCESSFUL-API-PASSWORD")
        initial = Credentials("fixture-first-user", "PRIVATE-INCORRECT-API-PASSWORD")
        selected = [initial]
        self.service.credentials_for = lambda ip: selected[0]
        self.service.credential_candidates = lambda ip, family: [initial, authenticated]
        self.service.remember_credentials = lambda ip, credentials: selected.__setitem__(0, credentials)
        original = self.transport.http_json
        def unlock(path, method="GET", **kwargs):
            if path == "/api/v1/unlock" and kwargs.get("payload", {}).get("pw") == initial.password:
                raise AuthenticationError(PRIVATE)
            return original(path, method, **kwargs)
        self.transport.http_json = unlock
        current = make_record(self.registry, "bitmain.vnish")
        current.identity.model = "Antminer " + authenticated.password
        current.identity.api_version = authenticated.username
        result = read_evidence(self.service, current, self.transport)
        self.assertNotIn(authenticated.password, json.dumps(result))
        self.assertNotIn(authenticated.username, json.dumps(result))
        self.assertEqual(selected[0], authenticated)

    def test_unknown_profile_or_custom_firmware_refused_before_any_io(self):
        for change in ("profile", "firmware", "make", "revision"):
            with self.subTest(change=change):
                current = make_record(self.registry, "elphapex.luci")
                if change == "profile":
                    current.identity.profile_id = "elphapex.luci.unreviewed"
                elif change == "firmware":
                    current.identity.firmware = "UnknownCustom"
                elif change == "make":
                    current.identity.make = "Unknown"
                else:
                    current.identity.profile_version += 1
                with self.assertRaises(PoolBackendError):
                    read_evidence(self.service, current, self.transport)
                self.assertFalse(self.transport.calls)

    def test_transport_auth_protocol_and_timeout_failures_use_fixed_safe_messages(self):
        from miner_scanner.runtime import ProtocolError
        for failure, expected in ((AuthenticationError(PRIVATE), "auth_required"),
                                  (ProtocolError(PRIVATE), "unsupported"),
                                  (requests.ReadTimeout(PRIVATE), "offline")):
            with self.subTest(failure=type(failure).__name__):
                self.transport.data[("GET", "/cgi-bin/get_miner_conf.cgi")] = failure
                with self.assertRaises(PoolBackendError) as problem:
                    self.read("bitmain.pitbit")
                self.assertEqual(problem.exception.status, expected)
                self.assertNotIn(PRIVATE, str(problem.exception))

    def test_cgminer_explicit_denial_is_not_an_observed_configuration(self):
        self.transport.data[("cgminer", "pools")] = {"STATUS": [{"STATUS": "E", "Msg": PRIVATE}]}
        with self.assertRaises(PoolBackendError) as problem:
            self.read("canaan.avalon")
        self.assertNotIn(PRIVATE, str(problem.exception))
        self.assertIn("configuration_schema", problem.exception.pool_evidence)
        self.assert_private_absent(problem.exception.pool_evidence)

    def test_missing_or_error_pool_structure_is_unsupported_but_keeps_safe_schema(self):
        cases = [
            ("bitmain.stock", ("GET", "/cgi-bin/get_miner_conf.cgi")),
            ("bitmain.pitbit", ("GET", "/cgi-bin/get_miner_conf.cgi")),
            ("elphapex.luci", ("GET", "/cgi-bin/luci/get_miner_conf.cgi")),
            ("canaan.avalon", ("cgminer", "pools")),
            ("jasminer.web", ("POST", "/cgi-bin/minerStatus.cgi")),
            ("bitmain.vnish", ("GET", "/api/v1/settings")),
        ]
        for profile, key in cases:
            for value in ({}, {"error": PRIVATE}, {"pools": "unknown"},
                          {"pools": [], "success": False}, {"pools": [], "code": -1}):
                with self.subTest(profile=profile, keys=list(value)):
                    self.transport = ReadTransport()
                    self.transport.data[key] = value
                    with self.assertRaises(PoolBackendError) as problem:
                        self.read(profile)
                    self.assertEqual(problem.exception.status, "unsupported")
                    safe = problem.exception.pool_evidence
                    self.assertEqual(safe["configuration_schema"]["kind"], "object")
                    self.assert_private_absent(safe)

    def test_explicit_empty_pool_lists_are_valid_read_data(self):
        cases = [
            ("bitmain.stock", ("GET", "/cgi-bin/get_miner_conf.cgi"), {"pools": []}),
            ("bitmain.pitbit", ("GET", "/cgi-bin/get_miner_conf.cgi"), {"pools": []}),
            ("elphapex.luci", ("GET", "/cgi-bin/luci/get_miner_conf.cgi"), {"pools": []}),
            ("canaan.avalon", ("cgminer", "pools"), {"POOLS": []}),
            ("jasminer.web", ("POST", "/cgi-bin/minerStatus.cgi"), {"pools": {"pool": []}}),
            ("bitmain.vnish", ("GET", "/api/v1/settings"), {"miner": {"pools": []}}),
            ("goldshell.mcb", ("GET", "/mcb/pools"), []),
            ("microbt.rpc3", ("rpc", "get.miner.status", "pools"),
             {"code": 0, "desc": "get.miner.status", "msg": {"pools": []}}),
        ]
        for profile, key, value in cases:
            with self.subTest(profile=profile):
                self.transport = ReadTransport()
                self.transport.data[key] = value
                result = self.read(profile)
                self.assertEqual(result["pool_slots"], [])
                self.assertIn("configuration_schema", result)

    def test_rpc_success_code_without_pool_list_is_not_observed(self):
        for msg in ({}, {"pools": "unknown"}, PRIVATE):
            with self.subTest(msg_type=type(msg).__name__):
                self.transport.data[("rpc", "get.miner.status", "pools")] = {
                    "code": 0, "desc": "get.miner.status", "msg": msg}
                with self.assertRaises(PoolBackendError) as problem:
                    self.read("microbt.rpc3")
                self.assertEqual(problem.exception.status, "unsupported")
                self.assert_private_absent(problem.exception.pool_evidence)

    def test_neither_report_export_nor_detached_evidence_triggers_more_requests(self):
        result = self.read("elphapex.luci")
        calls = len(self.transport.calls)
        with patch("socket.create_connection", side_effect=AssertionError("real network")), \
             patch("requests.sessions.Session.request", side_effect=AssertionError("real HTTP")):
            report = evidence_report([result])
        self.assertEqual(len(self.transport.calls), calls)
        self.assert_private_absent(report)


class PoolEvidenceCollectionIntegrationTests(unittest.TestCase):
    def test_explicit_read_collects_blocked_family_without_changes_or_journal(self):
        ip = "192.0.2.10"
        registry = ProfileRegistry()
        service = OfflineService({ip: OfflineDevice()})
        self.addCleanup(service.repository.close)
        service.registry = registry
        service.records[ip] = make_record(registry, "bitmain.pitbit")
        service.records[ip].identity.ip = ip
        transport = ReadTransport()
        service.transport_factory = lambda *args: transport
        plans = list(collect_pool_evidence_batch(service, [service.records[ip].to_legacy()]))
        self.assertEqual(plans[0].status, "observed", plans[0].message)
        self.assertIsNone(plans[0]._prepared)
        self.assertEqual(plans[0].changes, ())
        self.assertIsNone(service.repository.command_result(plans[0].command_id))
        self.assertEqual([key for key, _ in transport.calls], [("GET", "/cgi-bin/get_miner_conf.cgi")])
        self.assertEqual(plans[0].evidence["read_source"], "antminer.cgi")
        report = evidence_report(plans)
        self.assertEqual(report["devices"][0]["review_status"], "observed")
        self.assertNotIn(PRIVATE, json.dumps(report))

    def test_unknown_schema_collection_is_not_observed_but_exportable(self):
        ip = "192.0.2.10"
        registry = ProfileRegistry()
        service = OfflineService({ip: OfflineDevice()})
        self.addCleanup(service.repository.close)
        service.registry = registry
        service.records[ip] = make_record(registry, "bitmain.pitbit")
        service.records[ip].identity.ip = ip
        transport = ReadTransport()
        transport.data[("GET", "/cgi-bin/get_miner_conf.cgi")] = {PRIVATE: PRIVATE}
        service.transport_factory = lambda *args: transport
        plans = list(collect_pool_evidence_batch(service, [service.records[ip].to_legacy()]))
        self.assertEqual(plans[0].status, "unsupported")
        self.assertIsNone(plans[0]._prepared)
        self.assertIn("configuration_schema", plans[0].evidence)
        report = evidence_report(plans)
        self.assertEqual(report["devices"][0]["review_status"], "unsupported")
        self.assertNotIn(PRIVATE, json.dumps(report))
        self.assertIsNone(service.repository.command_result(plans[0].command_id))


if __name__ == "__main__":
    unittest.main()
