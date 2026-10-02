"""Synthetic regression cases, not certification of firmware builds."""
import json
import unittest
from copy import deepcopy
import tempfile
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from miner_scanner.drivers import make_record
from miner_scanner.normalization import format_rate, normalize
from miner_scanner.profiles import ProfileRegistry
from miner_scanner.protocol_compat import repair_antminer_stats
from miner_scanner.repository import DeviceRepository
from miner_scanner.runtime import AuthenticationError, Operation, ProtocolError
from miner_scanner.service import ScannerService
from miner_scanner.transports import Transport
from tests.fakes import FakeFactory
from tests.test_transports import FragmentSocket


class AntminerRegressionTests(unittest.TestCase):
    def test_z11_z15e_hardware_captures(self):
        fixture = Path(__file__).parent / "fixtures" / "antminer_z11_z15e_hardware.json"
        expected = {"Antminer Z11": ("138.27 kSol/s", "62 65 66", "3840 3840"),
                    "Antminer Z15e": ("306.69 kSol/s", "59 60 66", "4560 4560")}
        for data in json.loads(fixture.read_text(encoding="utf-8"))["cases"]:
            record = make_record(ProfileRegistry().resolve(data), "192.0.2.1", data)
            with self.subTest(model=record.identity.model):
                self.assertEqual(record.display["Status"], "Running")
                self.assertEqual(record.display["Algo"], "Equihash")
                self.assertEqual(tuple(record.display[k] for k in ("Real", "Temp", "Fan")), expected[record.identity.model])

    def test_six_hardware_captures(self):
        fixture = Path(__file__).parent / "fixtures" / "antminer_stock_hardware.json"
        expected = {
            "Antminer Z15": ("Running", "Equihash", "444.31 kSol/s", "63 68 63"),
            "Antminer Z15 Pro": ("Running", "Equihash", "629.01 kSol/s", "68 69 70"),
            "Antminer D9": ("Running", "X11", "1752.34 GH/s", "72 67 69"),
            "Antminer S21": ("Sleep", "SHA-256", "0.00 TH/s", ""),
            "Antminer T21": ("Sleep", "SHA-256", "0.00 TH/s", ""),
            "Antminer S21+": ("Sleep", "SHA-256", "0.00 TH/s", "27 27 27"),
        }
        observed = set()
        for data in json.loads(fixture.read_text(encoding="utf-8"))["cases"]:
            record = make_record(ProfileRegistry().resolve(data), "192.0.2.1", data)
            model = record.display["Model"]
            with self.subTest(model=model):
                self.assertEqual(tuple(record.display[k] for k in ("Status", "Algo", "Real", "Temp")), expected[model])
                self.assertEqual(record.display["Error"], "")
                if model == "Antminer Z15":
                    self.assertEqual([rpm for rpm in record.telemetry.fan_rpm if rpm > 0], [4200, 4200])
            observed.add(model)
        self.assertEqual(observed, set(expected))

    def test_hardware_s21_sleep_capture_has_no_sensor_fields(self):
        data = json.loads((Path(__file__).parent / "fixtures" / "antminer_s21_stock_sleep.json").read_text(encoding="utf-8"))
        record = make_record(ProfileRegistry().resolve(data), "192.0.2.1", data)
        self.assertEqual(record.display["Status"], "Sleep")
        self.assertEqual(record.display["Real"], "0.00 TH/s")
        self.assertEqual(record.display["Error"], "")
        self.assertEqual(record.telemetry.mining_state, "stopped")
        self.assertEqual(record.telemetry.temperatures_c, [])
        self.assertEqual(record.telemetry.fan_rpm, [])
        self.assertEqual(record.identity.firmware_version, "Fri Oct 11 16:34:48 CST 2024")

    def record(self, model, *, stats=None, summary=None, config=None):
        data = {"version": {"VERSION": [{"Type": model}]},
                "system": {"minertype": model},
                "stats": {"STATS": [stats or {}]},
                "summary": {"SUMMARY": [summary or {}]}}
        if config is not None:
            data["config"] = config
        factory = FakeFactory(data)
        repository = DeviceRepository(":memory:")
        try:
            result = ScannerService(repository=repository, transport_factory=factory).poll("192.0.2.1")
        finally:
            repository.close()
        self.assertFalse(factory.writes)
        return result

    def test_z15_model_from_version_controls_algorithm_and_units(self):
        record = self.record("Antminer Z15", summary={"GHS 5s": 420000, "GHS av": 410000},
                             stats={"fan3": "4800.0", "fan4": 4900,
                                    "temp2_1": "61-67-64", "temp2_2": [62, 68]})
        self.assertEqual(record.display["Algo"], "Equihash")
        self.assertEqual(record.display["Real"], "420.00 kSol/s")
        self.assertEqual(record.display["Avg"], "410.00 kSol/s")
        self.assertEqual(record.telemetry.rate, 420000)
        self.assertEqual(record.telemetry.rate_unit, "Sol/s")
        self.assertEqual(record.display["Fan"], "4800 4900")
        self.assertEqual(record.display["Temp"], "67 68")
        self.assertEqual(record.display["Status"], "Running")

    def test_z15_pro_legacy_fields_contain_kilosolutions(self):
        record = self.record("Antminer Z15 Pro", summary={"GHS 5s": 840, "GHS av": 830})
        self.assertEqual(record.display["Real"], "840.00 kSol/s")
        self.assertEqual(record.telemetry.rate, 840000)
        self.assertEqual(record.display["Avg"], "830.00 kSol/s")

    def test_unknown_equihash_model_needs_a_mapping(self):
        data = {"stats": {"STATS": [{"Type": "Antminer Z99", "GHS 5s": 123}]}}
        record = make_record(ProfileRegistry().by_id["bitmain.stock"], "192.0.2.1", data)
        self.assertEqual(record.telemetry.algorithm, "Equihash")
        self.assertEqual(record.telemetry.rate_unit, "Sol/s")
        self.assertIsNone(record.telemetry.rate)
        self.assertEqual(record.display["Real"], "—")

    def test_d9_display_does_not_change_numeric_base_unit(self):
        record = self.record("Antminer D9", summary={"GHS 5s": 1800, "GHS av": 1790})
        self.assertEqual(record.display["Algo"], "X11")
        self.assertEqual(record.display["Real"], "1800.00 GH/s")
        self.assertEqual(record.telemetry.rate, 1800e9)

    def test_sleep_uses_both_config_keys_with_or_without_counters(self):
        for model in ("Antminer S21+", "Antminer T21"):
            for key in ("bitmain-work-mode", "miner-mode"):
                for summary in ({}, {"GHS 5s": 0}, {"GHS 5s": 200000}):
                    with self.subTest(model=model, key=key, summary=summary):
                        record = self.record(model, summary=summary, config={key: "1"})
                        self.assertEqual(record.display["Status"], "Sleep")
                        self.assertEqual(record.telemetry.mining_state, "stopped")
                        self.assertEqual(record.display["Error"], "")

    def test_no_config_or_missing_counters_do_not_prove_error_or_sleep(self):
        for stats, summary, config in (({}, {}, None), ({}, {"GHS 5s": 0}, None),
                                       ({"fan_num": 0, "temp_max": 0}, {"GHS 5s": 0}, None),
                                       ({}, {}, {"miner-mode": 0})):
            with self.subTest(stats=stats, summary=summary, config=config):
                record = self.record("Antminer T21", stats=stats, summary=summary, config=config)
                self.assertEqual(record.display["Status"], "Unknown")

    def test_explicit_running_mode_with_zero_rate_is_an_error(self):
        record = self.record("Antminer S21+", summary={"GHS 5s": 0}, config={"miner-mode": 0})
        self.assertEqual(record.display["Status"], "Error")
        self.assertEqual(record.display["Error"], "NO HASH")

    def test_zero_and_low_rates_keep_algorithm_unit(self):
        self.assertEqual(format_rate(0, "Sol/s", "Equihash"), "0.00 kSol/s")
        self.assertEqual(format_rate(0, "H/s", "SHA-256"), "0.00 TH/s")
        self.assertEqual(format_rate(1e9, "H/s", "X11"), "1.00 GH/s")

    def test_profile_metric_overrides_stock_compatibility(self):
        snapshot = normalize({"stats": {"STATS": [{"GHS 5s": 420}]}},
                             {"Algo": "Equihash", "Model": "Antminer Z15"}, "antminer",
                             {"rate": {"path": ["stats", "STATS", 0, "GHS 5s"], "unit": "kSol/s"}})
        self.assertEqual(snapshot.rate, 420000)
        self.assertEqual(snapshot.rate_unit, "Sol/s")

    def test_legacy_stats_repair_preserves_strings_and_sensor_values(self):
        raw = json.dumps({"STATS": [{"Type": "Antminer Z15"},
                                   {"fan1": 4800, "temp2_1": "61-67-64", "note": '123 " untouched'}]})
        raw = raw.replace('4800,', '4800').replace('}]}', ',}]}')
        result = json.loads(repair_antminer_stats(raw))
        self.assertEqual(result["STATS"][1]["fan1"], 4800)
        self.assertEqual(result["STATS"][1]["temp2_1"], "61-67-64")
        self.assertEqual(result["STATS"][1]["note"], '123 " untouched')

    def test_repair_rejects_unknown_models_truncation_and_malformed_values(self):
        for raw in ('{"STATS":[{"Type":"Other","fan1":4800 "temp1":60}]}',
                    '{"STATS":[{"Type":"Antminer Z15","fan1":4800',
                    '{"STATS":[{"Type":"Antminer Z15","fan1":NaNxxx}]}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                repair_antminer_stats(raw)

    def test_stock_z15_adjacent_stats_objects(self):
        raw = '{"STATS":[{"Type":"Antminer Z15"}{"fan1":4200,"temp2_1":65}]}'
        result = json.loads(repair_antminer_stats(raw))
        self.assertEqual(result["STATS"], [{"Type": "Antminer Z15"}, {"fan1": 4200, "temp2_1": 65}])

    def test_physical_fan_channels_keep_zero_and_missing_sensor_distinct(self):
        for model, channels in (("Z11", ("fan1", "fan2")), ("Z15", ("fan3", "fan4")),
                                ("Z15e", ("fan3", "fan4")), ("Z15", ("fan1", "fan3"))):
            data = {"system": {"minertype": f"Antminer {model}", "macaddr": "02:00:00:00:00:01"},
                    "stats": {"STATS": [{f"fan{i}": 0 for i in range(1, 7)}]},
                    "summary": {"SUMMARY": [{"GHS 5s": 200000}]}}
            data["stats"]["STATS"][0].update(dict.fromkeys(channels, 4200))
            profile = ProfileRegistry().resolve(data)
            baseline = make_record(profile, "192.0.2.1", data)
            self.assertEqual(baseline.display["FanChannels"], list(channels))
            self.assertTrue(baseline.display["FanChannelsConfirmed"])
            for speeds, expected in (((0, 4200), "0 4200"), ((4200, 0), "4200 0"), ((0, 0), "0 0")):
                with self.subTest(model=model, speeds=speeds):
                    changed = deepcopy(data)
                    stats = changed["stats"]["STATS"][0]
                    stats.update(dict(zip(channels, speeds)))
                    stats["fan_num"] = 0  # Firmware counters cannot hide physical slots.
                    record = make_record(profile, "192.0.2.1", changed, baseline)
                    self.assertEqual(record.display["Fan"], expected)
                    self.assertEqual(record.telemetry.fan_rpm, list(speeds))
            missing = deepcopy(data)
            del missing["stats"]["STATS"][0][channels[0]]
            record = make_record(profile, "192.0.2.1", missing, baseline)
            self.assertEqual(record.display["Fan"], "— 4200")
            self.assertEqual(record.telemetry.fan_rpm, [4200])

    def test_unknown_first_scan_keeps_labelled_zero_channels(self):
        record = self.record("Antminer Z15", stats={"fan1": 0, "fan2": 0, "fan3": 4200, "fan4": 0},
                             summary={"GHS 5s": 200000})
        self.assertFalse(record.display["FanChannelsConfirmed"])
        self.assertEqual(record.display["Fan"], "fan1=0 fan2=0 fan3=4200 fan4=0")

    def test_fan_mapping_survives_restart_but_not_changed_identity(self):
        data = {"system": {"minertype": "Antminer Z15", "macaddr": "02:00:00:00:00:01"},
                "stats": {"STATS": [{"fan1": 4320, "fan2": 0, "fan3": 30600, "fan4": 0}]}}
        profile = ProfileRegistry().resolve(data)
        baseline = make_record(profile, "192.0.2.1", data)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "devices.sqlite3"
            repository = DeviceRepository(path)
            repository.save(baseline)
            repository.close()
            repository = DeviceRepository(path)
            previous = repository.get("192.0.2.1")
            repository.close()
        data["stats"]["STATS"][0]["fan3"] = 0
        record = make_record(profile, "192.0.2.1", data, previous)
        self.assertEqual(record.display["Fan"], "4320 0")
        data["system"]["macaddr"] = "02:00:00:00:00:02"
        record = make_record(profile, "192.0.2.1", data, previous)
        self.assertFalse(record.display["FanChannelsConfirmed"])
        self.assertIn("fan3=0", record.display["Fan"])

    def test_new_active_connector_does_not_get_hidden_by_cached_pair(self):
        data = {"system": {"minertype": "Antminer Z15", "macaddr": "02:00:00:00:00:01"},
                "stats": {"STATS": [{"fan1": 0, "fan2": 0, "fan3": 4000, "fan4": 4100}]}}
        profile = ProfileRegistry().resolve(data)
        previous = make_record(profile, "192.0.2.1", data)
        data["stats"]["STATS"][0].update(fan1=4300, fan3=0, fan4=0)
        record = make_record(profile, "192.0.2.1", data, previous)
        self.assertFalse(record.display["FanChannelsConfirmed"])
        self.assertIn("fan1=4300", record.display["Fan"])

    def test_z11_and_z15e_use_observed_legacy_stats_layout(self):
        for model in ("Z11", "Z15e"):
            raw = '{"STATS":[{"Type":"Antminer ' + model + '"}{"fan3":4200,"temp2_1":65,"GHS 5s":267374.01}]}'
            data = {"stats": json.loads(repair_antminer_stats(raw))}
            record = make_record(ProfileRegistry().resolve(data), "192.0.2.1", data)
            self.assertEqual(record.display["Algo"], "Equihash")
            self.assertEqual(record.display["Real"], "267.37 kSol/s")
            self.assertEqual(record.display["Temp"], "65")
            self.assertEqual(record.display["Status"], "Running")

    def test_missing_authorization_explains_unconfirmed_state_and_recovers(self):
        factory = FakeFactory({"version": {"VERSION": [{"Type": "Antminer T21"}]},
                               "summary": {"SUMMARY": [{"GHS 5s": 0}]},
                               "config": AuthenticationError("Required")})
        repository = DeviceRepository(":memory:")
        self.addCleanup(repository.close)
        service = ScannerService(repository=repository, transport_factory=factory)
        record = service.poll("192.0.2.1")
        self.assertEqual(record.display["Status"], "Unknown")
        self.assertEqual(record.display["Error"], "AUTH REQUIRED")
        factory.data["config"] = {"bitmain-work-mode": "1"}
        record = service.poll("192.0.2.1")
        self.assertEqual(record.display["Status"], "Sleep")
        self.assertEqual(record.display["Error"], "")
        self.assertFalse(factory.writes)

    def test_http_only_sleeping_miner_reports_auth_loss_and_recovers(self):
        factory = FakeFactory({"system": {"minertype": "Antminer L7"},
                               "config": {"bitmain-work-mode": 1}})
        repository = DeviceRepository(":memory:")
        self.addCleanup(repository.close)
        service = ScannerService(repository=repository, transport_factory=factory)
        previous = service.poll("192.0.2.1")
        self.assertEqual(previous.display["Status"], "Sleep")
        self.assertIsNone(previous.telemetry.rate)
        factory.data["system"] = AuthenticationError("Required")
        blocked = service.poll("192.0.2.1", force_identify=True)
        self.assertEqual(blocked.display["Error"], "AUTH REQUIRED")
        self.assertEqual(blocked.display["Status"], "Unknown")
        self.assertTrue(blocked.telemetry.stale)
        self.assertIn("system:auth_required", blocked.telemetry.diagnostics)
        self.assertNotIn("system:auth_required", previous.telemetry.diagnostics)
        factory.data["system"] = {"minertype": "Antminer L7"}
        recovered = service.poll("192.0.2.1")
        self.assertEqual(recovered.display["Status"], "Sleep")
        self.assertEqual(recovered.display["Error"], "")
        self.assertFalse(recovered.telemetry.stale)
        self.assertFalse(factory.writes)

    def test_unidentified_without_auth_failure_keeps_no_profile(self):
        factory = FakeFactory({"system": {"minertype": "Antminer L7"},
                               "config": {"bitmain-work-mode": 1}})
        repository = DeviceRepository(":memory:")
        self.addCleanup(repository.close)
        service = ScannerService(repository=repository, transport_factory=factory)
        service.poll("192.0.2.1")
        factory.data.clear()
        record = service.poll("192.0.2.1", force_identify=True)
        self.assertEqual(record.display["Error"], "NO PROFILE")
        self.assertTrue(record.telemetry.stale)

    def test_elphapex_display_preserves_brand_without_changing_identity(self):
        data = {"elphapex_stats": {"INFO": {"type": "DG1+"},
                                  "STATS": [{"rate_5s": 14000}]}}
        record = make_record(ProfileRegistry().resolve(data), "192.0.2.1", data)
        self.assertEqual(record.identity.model, "DG1+")
        self.assertEqual(record.to_legacy()["Model"], "Elphapex DG1+")
        self.assertEqual(record.to_legacy()["Make"], "Elphapex")

    def test_transport_repairs_only_complete_packets_and_records_compatibility(self):
        fragments = [b'{"STATS":[{"Type":"Antminer Z15"}',
                     b',{"fan1":4800 "temp1":60}]}\x00']
        with Transport("192.0.2.1", Operation()) as transport:
            with patch.object(transport, "connection", return_value=nullcontext(FragmentSocket(fragments))):
                result = transport.cgminer("stats", repair=repair_antminer_stats)
            self.assertEqual(result["STATS"][1]["fan1"], 4800)
            self.assertIn("cgminer:legacy_json_repaired", transport.operation.errors)
            with patch.object(transport, "connection", return_value=nullcontext(FragmentSocket(fragments))):
                with self.assertRaises(ProtocolError):
                    transport.cgminer("stats")


if __name__ == "__main__":
    unittest.main()
