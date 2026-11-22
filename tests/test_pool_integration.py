"""Real pool planner and ABI adapters joined through offline device transports.

Only identity polling and the network surface are simulated. Preparing, mapping,
writing, readback, command journaling and result classification use production
code. Configurations and addresses below are synthetic.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from threading import Event, Lock, RLock
import unittest
from unittest.mock import patch

import requests

from miner_scanner.models import Credentials, DeviceIdentity, DeviceRecord, TelemetrySnapshot
from miner_scanner.pool_configuration import PoolEdit, WorkerNaming, prepare_pool_batch, execute_pool_batch
from miner_scanner.repository import DeviceRepository
from miner_scanner.profiles import ProfileRegistry
from miner_scanner.runtime import ScanOptions
from miner_scanner import stock_compatibility
from tests.test_pool_backends import pools, settings_spec


def stock_configuration():
    # Audited L9 read schema: mode and frequency-level have different write keys;
    # algo/frequency/voltage are read-only in this web configuration form.
    return {"pools": pools(), "bitmain-work-mode": "0", "bitmain-fan-ctrl": False,
            "bitmain-fan-pwm": "85", "bitmain-freq-level": "100", "algo": "scrypt",
            "bitmain-freq": "0", "bitmain-voltage": "0"}


def vnish_configuration():
    return {"miner": {"pools": pools(), "cooling": {"target_temp": 70},
                      "misc": {"read_only_mode": False}},
            "network": {"hostname": "fixture-miner"}, "ui": {"theme": "dark"}}


class OfflineDevice:
    def __init__(self, firmware="Stock"):
        self.firmware = firmware
        self.config = stock_configuration() if firmware == "Stock" else vnish_configuration()
        self.spec = settings_spec()
        self.reply = (200, b'{"stats":"success","code":"M000"}') if firmware == "Stock" else (
            200, b'{"restart_required":false,"reboot_required":false}')
        self.apply = True
        self.write_error = None
        self.fail_readback = False
        self.after_write = None
        self.writes = []
        self.reads = []
        self.web = {}
        self.write_to_read = {"pools": "pools", "bitmain-fan-ctrl": "bitmain-fan-ctrl",
                              "bitmain-fan-pwm": "bitmain-fan-pwm", "freq-level": "bitmain-freq-level",
                              "miner-mode": "bitmain-work-mode"}


class OfflineTransport:
    def __init__(self, device, ip, operation):
        self.device, self.ip, self.operation = device, ip, operation

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def http_json(self, path, method="GET", **kwargs):
        self.operation.remaining()
        self.device.reads.append((path, method))
        if path in {"/cgi-bin/get_miner_conf.cgi", "/api/v1/settings"}:
            if self.device.writes and self.device.fail_readback:
                raise requests.ReadTimeout("synthetic-private-payload")
            return deepcopy(self.device.config)
        if path == "/cgi-bin/get_blink_status.cgi":
            return {"blink": False}
        if path == "/cgi-bin/get_adjustment_params.cgi":
            return {}
        if path == "/docs/api-doc.json":
            return deepcopy(self.device.spec)
        if path == "/api/v1/unlock":
            return {"token": "fixture-token-" + str(len(self.device.reads))}
        raise AssertionError("Unexpected API read")

    def http(self, path, method="GET", **kwargs):
        self.operation.remaining()
        if method == "GET" and (path in {"/miner.html", "/index.html"} or path in self.device.web):
            self.device.reads.append((path, method))
            return (200, self.device.web[path]) if path in self.device.web else (404, b"")
        if method != "POST" or path not in {"/cgi-bin/set_miner_conf.cgi", "/api/v1/settings"}:
            raise AssertionError("Unexpected API write")
        self.device.writes.append((path, method, deepcopy(kwargs)))
        payload = kwargs["payload"]
        if self.device.apply:
            if path == "/api/v1/settings":
                if set(payload) != {"miner"} or set(payload["miner"]) != {"pools"}:
                    raise AssertionError("Unrelated settings were included in VNish write")
                self.device.config["miner"]["pools"] = deepcopy(payload["miner"]["pools"])
            else:
                # Emulate the documented read/write ABI instead of updating the
                # read config with write aliases; incorrect mapping must fail.
                if set(payload) != set(self.device.write_to_read):
                    raise AssertionError("Stock write did not use the exact reviewed parameter map")
                for key, value in payload.items():
                    source = self.device.write_to_read[key]
                    self.device.config[source] = (str(value) if source == "bitmain-work-mode"
                                                   and isinstance(self.device.config[source], str)
                                                   else deepcopy(value))
        if self.device.after_write:
            self.device.after_write(self.device)
        if self.device.write_error:
            raise self.device.write_error
        return self.device.reply


class OfflineService:
    def __init__(self, devices):
        self.devices = devices
        self.records = {}
        self.guards = {ip: Lock() for ip in devices}
        self.locks = {ip: RLock() for ip in devices}
        self.options = ScanOptions()
        self.repository = DeviceRepository(":memory:")
        for ip, device in devices.items():
            firmware = device.firmware
            identity = DeviceIdentity(device_id="fixture-" + ip, ip=ip, make="Bitmain",
                model="Antminer L9", firmware=firmware, firmware_version="fixture-build",
                api_version="3.1", serial=None, mac=None,
                profile_id="bitmain.stock" if firmware == "Stock" else "bitmain.vnish",
                profile_version=1, fingerprint="fixture-passport-" + ip)
            self.records[ip] = DeviceRecord(identity, TelemetrySnapshot(), {})

    def get_record(self, ip):
        return self.records.get(ip)

    def poll(self, ip, **kwargs):
        # Identity polling is intentionally independent of pool settings: editing
        # a worker must not change the passport/fingerprint of the same ASIC.
        return self.records.get(ip)

    def credentials_for(self, ip):
        return Credentials("root", "fixture-access-password")

    def credential_candidates(self, ip, family):
        return [self.credentials_for(ip)]

    def remember_credentials(self, ip, credentials):
        pass

    def command_lock_for(self, ip):
        return self.guards[ip]

    def lock_for(self, ip):
        return self.locks[ip]

    def transport_factory(self, ip, operation, credentials):
        return OfflineTransport(self.devices[ip], ip, operation)


class PoolIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.service = OfflineService({"192.0.2.10": OfflineDevice(),
                                       "192.0.2.2": OfflineDevice("VNish")})
        self.addCleanup(self.service.repository.close)
        pause = patch("miner_scanner.pool_configuration.Operation.pause", return_value=None)
        pause.start()
        self.addCleanup(pause.stop)

    def prepare(self, edits=None, ips=("192.0.2.10",)):
        rows = [self.service.records[ip].to_legacy() for ip in ips]
        return list(prepare_pool_batch(self.service, rows,
            edits or [PoolEdit(0, worker=WorkerNaming("TEST", width=3))]))

    def apply(self, plans, **kwargs):
        return dict(execute_pool_batch(self.service, plans, **kwargs))

    def test_stock_mapping_round_trip_preserves_unedited_pools_and_read_only_fields(self):
        device = self.service.devices["192.0.2.10"]
        baseline = deepcopy(device.config)
        plans = self.prepare()
        self.assertEqual(plans[0].status, "ready", plans[0].message)
        self.assertFalse(device.writes)
        result = self.apply(plans)[plans[0].ip]
        self.assertEqual(result.status, "succeeded", result.message)
        self.assertTrue(result.accepted_by_api)
        expected = deepcopy(baseline)
        expected["pools"][0]["user"] = "TEST.001"
        self.assertEqual(device.config, expected)
        payload = device.writes[0][2]["payload"]
        self.assertEqual(set(payload), {"pools", "bitmain-fan-ctrl", "bitmain-fan-pwm", "freq-level", "miner-mode"})
        self.assertEqual(payload["freq-level"], "100")
        self.assertEqual(payload["miner-mode"], 0)
        self.assertEqual(self.service.records[plans[0].ip].display["Worker"], "TEST.001")
        self.assertEqual(len(device.writes), 1)
        self.assertNotIn("pool-secret", repr(plans))
        self.assertNotIn("pool-secret", repr(result))

    def test_vnish_minimal_settings_round_trip_and_unrelated_config_preserved(self):
        ip = "192.0.2.2"
        device = self.service.devices[ip]
        baseline = deepcopy(device.config)
        plans = self.prepare(ips=(ip,))
        self.assertEqual(plans[0].status, "ready", plans[0].message)
        self.assertEqual(self.apply(plans)[ip].status, "succeeded")
        expected = deepcopy(baseline)
        expected["miner"]["pools"][0]["user"] = "TEST.001"
        self.assertEqual(device.config, expected)
        self.assertEqual(device.writes[0][2]["payload"], {"miner": {"pools": expected["miner"]["pools"]}})
        self.assertEqual(len(device.writes), 1)

    def test_vnish_current_published_full_schema_through_real_planner_and_backend(self):
        ip = "192.0.2.2"
        device = self.service.devices[ip]
        source = Path(__file__).resolve().parents[1] / "docs" / "vnish_openapi.json"
        device.spec = json.loads(source.read_text(encoding="utf-8"))
        device.config["regional"] = {"timezone": "UTC"}
        baseline = deepcopy(device.config)
        plans = self.prepare([PoolEdit(2, worker=WorkerNaming("BACKUP", "ip"))], ips=(ip,))
        self.assertEqual(plans[0].status, "ready", plans[0].message)
        self.assertEqual(self.apply(plans)[ip].status, "succeeded")
        expected = deepcopy(baseline)
        expected["miner"]["pools"][2]["user"] = "BACKUP.192_0_2_2"
        self.assertEqual(device.config, expected)
        self.assertEqual(len(device.writes), 1)

    def test_every_audited_stock_ui_and_model_preserves_configuration(self):
        """All current pool maps, including KS5 UI with no power-mode actions.

        Synthetic static assets stand in for the reviewed digests, as existing
        stock interface tests do. The real bounded probe hashes them, then the
        real adapter selects the unchanged production parameter maps.
        """
        catalog = deepcopy(stock_compatibility.interfaces())
        original = deepcopy(catalog["miner"])
        synthetic = {}
        assets = {}
        for digest, definition in original.items():
            asset = definition["source_asset"]
            script = ("/* offline reviewed interface " + asset + " */").encode()
            page = ('<script src="/js/' + asset + '"></script>').encode()
            fixture_digest = hashlib.sha256(script).hexdigest()
            fixture_definition = deepcopy(definition)
            if "pages" in fixture_definition:
                fixture_definition["pages"] = [hashlib.sha256(page).hexdigest()]
            synthetic[fixture_digest] = fixture_definition
            assets[digest] = {"/miner.html": page, "/js/" + asset: script}
        catalog["miner"] = synthetic
        source_values = {
            "bitmain-fan-ctrl": True, "bitmain-fan-pwm": "78", "bitmain-user-ip-cat": False,
            "bitmain-freq-level": "100", "bitmain-hashrate-percent": "88", "bitmain-freq": "0",
            "bitmain-power-target": "4200",
            "bitmain-voltage": "0", "bitmain-chip-temp-threshold": 97,
            "bitmain-drop-freq-scale": 100, "bitmain-power-off-delay": 0, "algo": "fixture-algorithm",
            "api-listen": True, "api-network": False, "api-groups": "synthetic-api-groups",
            "api-allow": "synthetic-api-allow", "bitmain-use-vil": False,
            "bitmain-ccdelay": "synthetic-delay", "bitmain-pwth": "synthetic-power-threshold",
        }
        ip = "192.0.2.10"
        device = self.service.devices[ip]
        covered = set()
        saw_no_power_actions = False
        with patch.object(stock_compatibility, "interfaces", return_value=catalog):
            for digest, definition in original.items():
                for model in definition["modes"]:
                    for include_optional in (False, True):
                        with self.subTest(asset=definition["source_asset"], model=model, optional=include_optional):
                            mapping = dict(definition["required"])
                            if include_optional:
                                mapping.update(definition.get("optional", {}))
                            sources = set(mapping.values()) | set(definition.get("read_only", []))
                            config = {source: deepcopy(pools() if source == "pools" else source_values[source])
                                      for source in sources}
                            config["bitmain-work-mode"] = "1"
                            device.config = deepcopy(config)
                            device.web = deepcopy(assets[digest])
                            device.writes.clear()
                            device.write_to_read = {**mapping, definition["write_mode"]: "bitmain-work-mode"}
                            identity = self.service.records[ip].identity
                            identity.model = "Antminer " + model
                            identity.firmware_version = "arbitrary-build-" + str(include_optional)
                            identity.fingerprint = "offline-" + digest + "-" + model + "-" + str(include_optional)
                            plans = self.prepare([PoolEdit(0, worker=WorkerNaming("TEST", width=3))])
                            if definition.get("mode_update_only"):
                                self.assertEqual(plans[0].status, "unsupported")
                                self.assertFalse(device.writes)
                                covered.add((digest, model))
                                continue
                            self.assertEqual(plans[0].status, "ready", plans[0].message)
                            result = self.apply(plans)[ip]
                            self.assertEqual(result.status, "succeeded", result.message)
                            expected = deepcopy(config)
                            expected["pools"][0]["user"] = "TEST.001"
                            self.assertEqual(device.config, expected)
                            self.assertEqual(len(device.writes), 1)
                            payload = device.writes[0][2]["payload"]
                            self.assertEqual(payload[definition["write_mode"]],
                                             "1" if definition.get("preserve_mode_type") else 1)
                            self.assertEqual(payload["pools"][1:], pools()[1:])
                            self.assertEqual(set(payload), set(mapping) | {definition["write_mode"]})
                            for target, source in mapping.items():
                                if source != "pools":
                                    self.assertEqual(payload[target], config[source])
                            covered.add((digest, model))
                            saw_no_power_actions |= not definition["modes"][model]
                            # Exact UI evidence is insufficient for an unknown
                            # configuration field: do not drop it in a full write.
                            device.config = {**deepcopy(config), "unknown-new-setting": "preserve-me"}
                            device.writes.clear()
                            refused = self.prepare()
                            self.assertEqual(refused[0].status, "unsupported")
                            self.assertFalse(device.writes)
        self.assertEqual(covered, {(digest, model) for digest, definition in original.items()
                                   for model in definition["modes"]})
        self.assertTrue(saw_no_power_actions, "Known KS5 UI without power actions must still test pool writes")

    def test_stock_models_without_audited_pool_map_do_not_inherit_other_command_support(self):
        ip = "192.0.2.10"
        device = self.service.devices[ip]
        for model in ("S19", "D7", "Z11", "A3"):
            with self.subTest(model=model):
                self.service.records[ip].identity.model = "Antminer " + model
                self.service.records[ip].identity.fingerprint = "offline-unmapped-" + model
                device.config = stock_configuration()
                plans = self.prepare()
                self.assertEqual(plans[0].status, "unsupported")
                self.assertFalse(device.writes)

    def test_every_exact_stock_profile_version_uses_pool_contract_evidence(self):
        ip = "192.0.2.10"
        device = self.service.devices[ip]
        profiles = [profile for profile in ProfileRegistry().profiles
                    if profile.make == "Bitmain" and profile.firmware == "Stock" and profile.match]
        seen = set()
        for profile in profiles:
            for model in profile.match.get("models", []):
                with self.subTest(profile=profile.id, model=model):
                    identity = self.service.records[ip].identity
                    identity.profile_id = profile.id
                    identity.profile_version = profile.version
                    identity.model = model
                    identity.fingerprint = "offline-profile-" + profile.id
                    identity.firmware_version = (profile.match.get("firmware_versions")
                                                 or profile.match.get("compile_times") or ["fixture-build"])[0]
                    identity.api_version = profile.match.get("api_versions", ["3.1"])[0]
                    device.config = stock_configuration()
                    device.writes.clear()
                    device.write_to_read = {"pools": "pools", "bitmain-fan-ctrl": "bitmain-fan-ctrl",
                        "bitmain-fan-pwm": "bitmain-fan-pwm", "freq-level": "bitmain-freq-level",
                        "miner-mode": "bitmain-work-mode"}
                    if model in {"Antminer S21", "Antminer T21"}:
                        device.config = {"pools": pools(), "bitmain-work-mode": "1", "bitmain-fan-ctrl": True,
                                         "bitmain-fan-pwm": "78", "bitmain-hashrate-percent": "88"}
                        device.write_to_read.pop("freq-level")
                        device.write_to_read["bitmain-hashrate-percent"] = "bitmain-hashrate-percent"
                    plans = self.prepare()
                    if model in {"Antminer S19", "Antminer Z11"}:
                        # Hardware verification for sleep/LED never grants an
                        # unreviewed full pool-configuration write operation.
                        self.assertEqual(plans[0].status, "unsupported")
                        self.assertFalse(device.writes)
                    else:
                        baseline = deepcopy(device.config)
                        self.assertEqual(plans[0].status, "ready", plans[0].message)
                        self.assertEqual(self.apply(plans)[ip].status, "succeeded")
                        baseline["pools"][0]["user"] = "TEST.001"
                        self.assertEqual(device.config, baseline)
                        self.assertEqual(len(device.writes), 1)
                    seen.add(profile.id)
        self.assertEqual(seen, {profile.id for profile in profiles})

    def test_vnish_saved_configuration_requiring_restart_is_not_reported_applied(self):
        ip = "192.0.2.2"
        device = self.service.devices[ip]
        for key in ("restart_required", "reboot_required"):
            with self.subTest(flag=key):
                device.config = vnish_configuration()
                device.writes.clear()
                flags = {"restart_required": False, "reboot_required": False}
                flags[key] = True
                device.reply = (200, json.dumps(flags).encode())
                plans = self.prepare(ips=(ip,))
                result = self.apply(plans)[ip]
                self.assertEqual(result.status, "unconfirmed")
                self.assertTrue(result.accepted_by_api)
                self.assertIn("сохранены и прочитаны", result.message)
                self.assertEqual(len(device.writes), 1)
                self.assertEqual(device.config["miner"]["pools"][0]["user"], "TEST.001")

    def test_external_configuration_change_after_preview_stops_before_write(self):
        device = self.service.devices["192.0.2.10"]
        plans = self.prepare()
        device.config["bitmain-fan-pwm"] = "100"
        result = self.apply(plans)[plans[0].ip]
        self.assertEqual(result.status, "skipped")
        self.assertFalse(result.accepted_by_api)
        self.assertFalse(device.writes)
        self.assertEqual(device.config["pools"][0]["user"], "OWNER.old1")

    def test_masked_unrelated_setting_never_reaches_ready_or_command_journal(self):
        device = self.service.devices["192.0.2.10"]
        device.config["bitmain-fan-pwm"] = "********"
        plans = self.prepare()
        self.assertEqual(plans[0].status, "unsupported")
        self.assertFalse(device.writes)
        self.assertIsNone(self.service.repository.command_result(plans[0].command_id))

    def test_vnish_disabled_slot_missing_password_blocks_ready_until_explicitly_filled(self):
        ip = "192.0.2.2"
        device = self.service.devices[ip]
        device.config["miner"]["pools"][1].pop("pass")
        refused = self.prepare(ips=(ip,))
        self.assertEqual(refused[0].status, "unsupported")
        self.assertFalse(device.writes)
        self.assertIsNone(self.service.repository.command_result(refused[0].command_id))
        prepared = self.prepare([PoolEdit(0, worker=WorkerNaming("TEST")), PoolEdit(1, password="")], ips=(ip,))
        self.assertEqual(prepared[0].status, "ready", prepared[0].message)
        self.assertEqual(self.apply(prepared)[ip].status, "succeeded")
        self.assertEqual(device.config["miner"]["pools"][1], {"url": "", "user": "", "pass": ""})

    def test_unrelated_configuration_corruption_after_write_prevents_success(self):
        device = self.service.devices["192.0.2.10"]
        plans = self.prepare()
        device.after_write = lambda device: device.config.update({"bitmain-fan-pwm": "100"})
        result = self.apply(plans)[plans[0].ip]
        self.assertEqual(result.status, "unconfirmed")
        self.assertTrue(result.accepted_by_api)
        self.assertEqual(len(device.writes), 1)

    def test_unknown_ack_without_readback_never_claims_api_acceptance(self):
        device = self.service.devices["192.0.2.10"]
        for raw in (b"{}", b'{"unrelated":true}', b'{"code":0.0}'):
            with self.subTest(raw=raw):
                device.config = stock_configuration()
                device.writes.clear()
                device.apply = False
                device.reply = (200, raw)
                plans = self.prepare()
                result = self.apply(plans)[plans[0].ip]
                self.assertEqual(result.status, "unconfirmed")
                self.assertFalse(result.accepted_by_api)
                self.assertEqual(len(device.writes), 1)

    def test_unknown_ack_can_be_confirmed_by_actual_stored_configuration(self):
        device = self.service.devices["192.0.2.10"]
        device.reply = (200, b"{}")
        plans = self.prepare()
        result = self.apply(plans)[plans[0].ip]
        self.assertEqual(result.status, "succeeded")
        self.assertFalse(result.accepted_by_api)
        self.assertEqual(len(device.writes), 1)

    def test_explicit_rejection_does_not_poll_or_retry_configuration_write(self):
        device = self.service.devices["192.0.2.10"]
        plans = self.prepare()
        device.apply = False
        device.reply = (200, b'{"stats":"error","code":"M001"}')
        before_reads = len(device.reads)
        result = self.apply(plans)[plans[0].ip]
        self.assertEqual(result.status, "failed")
        self.assertFalse(result.accepted_by_api)
        self.assertEqual(len(device.writes), 1)
        # Exactly one recheck before dispatch; no post-rejection readback loop.
        self.assertEqual(sum(path == "/cgi-bin/get_miner_conf.cgi" for path, _ in device.reads[before_reads:]), 1)

    def test_timeout_after_successful_write_uses_readback_without_replay(self):
        device = self.service.devices["192.0.2.10"]
        plans = self.prepare()
        device.write_error = requests.ReadTimeout("synthetic-private-payload")
        result = self.apply(plans)[plans[0].ip]
        self.assertEqual(result.status, "succeeded")
        self.assertFalse(result.accepted_by_api)
        self.assertEqual(len(device.writes), 1)
        self.assertNotIn("synthetic-private-payload", result.message)
        self.assertEqual(self.apply(plans)[plans[0].ip], result)
        self.assertEqual(len(device.writes), 1)

    def test_cancel_after_write_remains_uncertain_and_releases_command_lock(self):
        device = self.service.devices["192.0.2.10"]
        plans = self.prepare()
        cancel = Event()
        device.after_write = lambda device: cancel.set()
        result = self.apply(plans, cancel=cancel)[plans[0].ip]
        self.assertEqual(result.status, "unconfirmed")
        self.assertEqual(len(device.writes), 1)
        lock = self.service.command_lock_for(plans[0].ip)
        self.assertTrue(lock.acquire(blocking=False))
        lock.release()
        self.assertEqual(self.apply(plans)[plans[0].ip], result)
        self.assertEqual(len(device.writes), 1)

    def test_cancel_before_dispatch_sends_no_write(self):
        plans = self.prepare()
        cancel = Event()
        cancel.set()
        self.assertEqual(self.apply(plans, cancel=cancel)[plans[0].ip].status, "cancelled")
        self.assertFalse(self.service.devices[plans[0].ip].writes)

    def test_mixed_apis_keep_numbering_when_one_write_fails(self):
        stock_device = self.service.devices["192.0.2.10"]
        vnish_device = self.service.devices["192.0.2.2"]
        plans = self.prepare(ips=("192.0.2.10", "192.0.2.2"))
        self.assertTrue(all(plan.status == "ready" for plan in plans), repr(plans))
        stock_device.apply = False
        stock_device.reply = (403, b"denied")
        results = self.apply(plans)
        self.assertEqual(results["192.0.2.10"].status, "failed")
        self.assertEqual(results["192.0.2.2"].status, "succeeded")
        self.assertEqual(vnish_device.config["miner"]["pools"][0]["user"], "TEST.001")
        self.assertEqual(stock_device.writes[0][2]["payload"]["pools"][0]["user"], "TEST.002")


if __name__ == "__main__":
    unittest.main()
