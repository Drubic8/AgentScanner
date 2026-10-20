"""KS5 stock mode conflicts: sanitized live reads and offline regressions."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from miner_scanner.drivers import make_record
from miner_scanner.profiles import ProfileRegistry
from miner_scanner.repository import DeviceRepository
from miner_scanner.service import ScannerService
from tests.fakes import FakeFactory


class KS5StateTests(unittest.TestCase):
    def setUp(self):
        self.cases = json.loads((Path(__file__).parent / 'fixtures' /
                                'antminer_ks5_running_mode_one.json').read_text(encoding='utf-8'))['cases']

    def record(self, data):
        return make_record(ProfileRegistry().resolve(data), '192.0.2.1', data)

    def test_real_builds_with_mode_one_and_current_hashrate_are_running(self):
        for data in self.cases:
            with self.subTest(build=data['system']['system_filesystem_version']):
                record = self.record(data)
                self.assertEqual(record.display['Status'], 'Running')
                self.assertEqual(record.telemetry.mining_state, 'running')
                self.assertGreater(record.telemetry.rate, 0)
                self.assertEqual(record.display['Error'], '')
                self.assertEqual(record.telemetry.rate,
                                 data['summary']['SUMMARY'][0]['GHS 5s'] * 1e9)

    def test_ks5_pro_uses_the_same_current_rate_rule(self):
        data = deepcopy(self.cases[0])
        data['system']['minertype'] = 'Antminer KS5 Pro'
        self.assertEqual(self.record(data).display['Status'], 'Running')

    def test_average_alone_never_overrides_sleep(self):
        for current in (0, None):
            data = deepcopy(self.cases[0])
            for source, key in (('summary', 'SUMMARY'), ('stats', 'STATS')):
                for item in data[source][key]:
                    item.pop('GHS 5s', None)
                    if current is not None:
                        item['GHS 5s'] = current
                    self.assertGreater(item.get('GHS av', 1), 0)
            with self.subTest(current=current):
                record = self.record(data)
                self.assertEqual(record.display['Status'], 'Sleep')
                self.assertEqual(record.telemetry.mining_state, 'stopped')

    def test_other_stock_models_keep_mode_precedence(self):
        for model in ('Antminer T21', 'Antminer S21', 'Antminer S21+', 'Antminer L9'):
            data = deepcopy(self.cases[0])
            data['system']['minertype'] = model
            with self.subTest(model=model):
                self.assertEqual(self.record(data).display['Status'], 'Sleep')

    def test_hardware_warning_is_preserved_for_hashing_ks5(self):
        data = deepcopy(self.cases[0])
        data['stats']['STATS'][-1]['chain_acs1'] = 'ooxo'
        record = self.record(data)
        self.assertEqual(record.display['Status'], 'Running')
        self.assertIn('HW ERR (B1)', record.display['Error'])

    def test_poll_refreshes_running_sleep_running_without_writes(self):
        data = deepcopy(self.cases[0])
        factory = FakeFactory(data)
        repository = DeviceRepository(':memory:')
        self.addCleanup(repository.close)
        service = ScannerService(repository=repository, transport_factory=factory)
        for expected, rate in (('Running', 14000), ('Sleep', 0), ('Running', 15000)):
            for source, key in (('summary', 'SUMMARY'), ('stats', 'STATS')):
                for item in factory.data[source][key]:
                    item['GHS 5s'] = rate
            record = service.poll('192.0.2.1')
            self.assertEqual(record.display['Status'], expected)
        self.assertFalse(factory.writes)


if __name__ == '__main__':
    unittest.main()
