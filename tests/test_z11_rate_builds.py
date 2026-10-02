"""Captured Z11 builds use different scales despite identical API/Miner versions."""
import json
import unittest
from contextlib import closing
from copy import deepcopy
from pathlib import Path

from miner_scanner.drivers import make_record
from miner_scanner.profiles import ProfileRegistry
from miner_scanner.repository import DeviceRepository
from miner_scanner.service import ScannerService
from tests.fakes import FakeFactory


class Z11RateBuildTests(unittest.TestCase):
    def setUp(self):
        self.old, self.new = json.loads((Path(__file__).parent / 'fixtures' /
            'antminer_z11_rate_builds.json').read_text(encoding='utf-8'))['cases']
        self.registry = ProfileRegistry()

    def record(self, data):
        return make_record(self.registry.resolve(data), '192.0.2.1', data)

    def test_captured_builds_through_scanner(self):
        for data, expected, average in ((self.old, '227.40 kSol/s', '70.60 kSol/s'),
                                        (self.new, '147.77 kSol/s', '140.48 kSol/s')):
            with self.subTest(build=data['system']['system_filesystem_version']):
                factory = FakeFactory(data)
                with closing(DeviceRepository(':memory:')) as repository:
                    record = ScannerService(repository=repository, transport_factory=factory).poll('192.0.2.1')
                self.assertEqual(record.display['Real'], expected)
                self.assertEqual(record.display['Avg'], average)
                self.assertEqual(record.telemetry.rate_unit, 'Sol/s')
                self.assertFalse(factory.writes)
        self.assertEqual(self.record(self.old).telemetry.rate, 227400)
        self.assertEqual(self.record(self.new).telemetry.rate, 147769.98)

    def test_cgminer_build_identifies_without_http(self):
        del self.old['system']
        self.assertEqual(self.record(self.old).display['Real'], '227.40 kSol/s')

    def test_missing_summary_falls_back_to_stats(self):
        del self.old['summary']
        self.assertEqual(self.record(self.old).display['Real'], '227.40 kSol/s')
        self.assertEqual(self.record(self.old).display['Avg'], '70.60 kSol/s')

    def test_low_zero_and_missing_rates_are_not_guessed(self):
        for value, expected in ((0, '0.00 kSol/s'), (0.24, '0.24 kSol/s'), (None, '—')):
            data = deepcopy(self.old)
            for block in (data['summary']['SUMMARY'][0], data['stats']['STATS'][1]):
                block['GHS 5s'] = block['GHS av'] = value
            record = self.record(data)
            self.assertEqual(record.display['Real'], expected)
            self.assertEqual(record.display['Avg'], expected)
        # A genuine low rate on the newer build must not be multiplied by 1000.
        self.new['summary']['SUMMARY'][0]['GHS 5s'] = 240
        self.assertEqual(self.record(self.new).display['Real'], '0.24 kSol/s')

    def test_zero_summary_wins_over_nonzero_stats(self):
        self.old['summary']['SUMMARY'][0]['GHS 5s'] = 0
        self.assertEqual(self.record(self.old).display['Real'], '0.00 kSol/s')

    def test_mapping_is_limited_to_model_build_and_api(self):
        for field, replacement in (('Type', 'Antminer Z15'), ('CompileTime', 'unknown'), ('API', '9.9')):
            data = deepcopy(self.old)
            data['version']['VERSION'][0][field] = replacement
            self.assertNotEqual(self.registry.resolve(data).id, 'bitmain.stock.z11.20190423')
        self.assertFalse(self.registry.resolve(self.old).verified_commands)


if __name__ == '__main__':
    unittest.main()
