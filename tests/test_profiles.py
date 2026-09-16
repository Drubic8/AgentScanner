from copy import deepcopy
import unittest

from miner_scanner.drivers import make_record
from miner_scanner.profiles import ProfileRegistry
from tests.fakes import stock


class ProfileFixturesTests(unittest.TestCase):
    def test_each_migrated_family_recognizes_structured_example(self):
        cases = {
            "bitmain.stock": stock(),
            "bitmain.vnish": {"stats": {"STATS": [{"Type": "Antminer S19 VNish"}]}, "vnish_info": {"fw_name": "VNish", "fw_version": "example"}, "vnish_summary": {"miner": {"miner_status": {"miner_state": "stopped"}}}},
            "bitmain.pitbit": {"version": {"VERSION": [{"Type": "Antminer S21 PitBit"}]}, "stats": {"STATS": [{"Type": "Antminer S21 PitBit", "Elapsed": 600}]}, "config": {"bitmain-work-mode": "1"}},
            "microbt.rpc3": {"rpc_info": {"code": 0, "msg": {"miner": {"type": "M50", "working": True}}}, "rpc_summary": {"code": 0, "msg": {"summary": {"GHS 5s": 120000}}}},
            "canaan.avalon": {"version": {"VERSION": [{"PROD": "Avalon 1366"}]}, "stats": {"STATS": [{"GHSspd": "120000"}]}},
            "elphapex.luci": {"elphapex_stats": {"INFO": {"type": "DG1"}, "STATS": [{"rate_5s": 14000, "elapsed": 600}]}},
            "ipollo.cgminer": {"stats": {"STATS": [{"ID": "G220", "Algo": "ethash", "Unit": "M/s", "Hashrate": 37.5}]}},
            "jasminer.web": {"jasminer_status": {"summary": {"miner": "Jasminer X16", "rt": "1900 MH/s", "avg": "1850 MH/s"}, "boards": {}, "pools": {}}},
            "generic.cgminer": {"stats": {"STATS": [{"Type": "Unknown ASIC"}]}},
            "generic.cgminer_web": {"web_status": '<cite id="bb_elapsed">1h</cite>'},
        }
        registry = ProfileRegistry()
        for expected, data in cases.items():
            with self.subTest(profile=expected):
                profile = registry.resolve(data)
                self.assertIsNotNone(profile)
                self.assertEqual(profile.id, expected)
                original = deepcopy(data)
                record = make_record(profile, "192.0.2.1", data)
                self.assertEqual(record.identity.profile_id, expected)
                self.assertEqual(data, original, "Parser mutated the response")
                self.assertIsInstance(record.to_legacy(), dict)

    def test_plain_web_server_and_unrelated_rpc_are_not_asics(self):
        registry = ProfileRegistry()
        for data in ({"web_status": "Hello"}, {"rpc_info": {"code": 0, "msg": {"printer": "online"}}}, {"system": {"model": "Router"}}):
            self.assertIsNone(registry.resolve(data))
