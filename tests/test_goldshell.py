"""Goldshell HS5 capture and bounded, read-only polling; no real network."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from miner_scanner.diagnostics import Capture, RecordingTransport
from miner_scanner.drivers import make_record
from miner_scanner.profiles import ProfileRegistry
from miner_scanner.repository import DeviceRepository
from miner_scanner.runtime import Operation
from miner_scanner.service import ScannerService
from tests.fakes import FakeFactory


class GoldshellTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).parent / 'fixtures' / 'goldshell_hs5_mcb.json'
        self.data = json.loads(path.read_text(encoding='utf-8'))['data']
        self.profile = ProfileRegistry().resolve(self.data)

    def record(self):
        return make_record(self.profile, '192.0.2.13', self.data)

    def service(self, factory):
        repository = DeviceRepository(':memory:')
        self.addCleanup(repository.close)
        return ScannerService(repository=repository, transport_factory=factory)

    def test_hardware_capture_identity_and_board_totals(self):
        original = deepcopy(self.data)
        record = self.record()
        self.assertEqual(record.identity.profile_id, 'goldshell.mcb')
        self.assertEqual(record.identity.make, 'Goldshell')
        self.assertEqual(record.identity.model, 'Goldshell-HS5')
        self.assertEqual(record.identity.firmware_version, '2.2.1')
        self.assertEqual(record.display['Algo'], 'Blake2B-Sia')
        self.assertEqual(record.display['Real'], '5082.35 GH/s')
        self.assertEqual(record.display['Avg'], '5344.04 GH/s')
        self.assertAlmostEqual(record.telemetry.rate, 5082.346485e9)
        self.assertAlmostEqual(record.telemetry.average_rate, 5344.044645e9)
        self.assertEqual(record.telemetry.uptime_seconds, 41 * 60)
        self.assertEqual(record.display['Status'], 'Running')
        self.assertEqual(record.telemetry.mining_state, 'running')
        self.assertEqual(record.display['Error'], '')
        self.assertEqual(self.data, original)

    def test_repeated_physical_fans_are_not_counted_for_each_board(self):
        record = self.record()
        self.assertEqual(record.telemetry.fan_rpm, [5580, 4860, 4860, 5100])
        self.assertEqual(record.display['Fan'], '5580 4860 4860 5100')
        self.assertEqual(record.telemetry.temperatures_c, [49, 44.4, 47, 45.8, 49, 45.3, 56, 49.7])

    def test_selected_algorithm_uses_name_not_fixed_id_or_model(self):
        config = self.data['goldshell_algo']
        config['algos'] = [{'name': 'blake2bsha3(HNS)', 'id': 7}, {'name': 'blake2b(SC)', 'id': 12}]
        config['algo_select'] = 7
        self.assertEqual(self.record().display['Algo'], 'Blake2B+SHA3')
        config['algo_select'] = 12
        self.assertEqual(self.record().display['Algo'], 'Blake2B-Sia')
        config['algo_select'] = 0
        self.assertEqual(self.record().display['Algo'], 'Unknown')
        del self.data['goldshell_algo']
        self.assertEqual(self.record().display['Algo'], 'Unknown')

    def test_zero_and_missing_board_rates_are_distinct(self):
        rows = self.data['goldshell_devs']['data']
        for board in rows:
            board['hashrate'] = 0
        record = self.record()
        self.assertEqual(record.display['Real'], '0.00 GH/s')
        self.assertEqual(record.display['Status'], 'Unknown')
        self.assertEqual(record.telemetry.mining_state, 'unknown')
        del rows[0]['hashrate']
        record = self.record()
        self.assertIsNone(record.telemetry.rate)
        self.assertEqual(record.display['Real'], '—')

    def test_zero_rpm_and_invalid_sensor_text(self):
        board = self.data['goldshell_devs']['data'][0]
        board['fanspeed'] = '0 rpm / 4860 rpm / 0 rpm / 5100 rpm'
        board['temp'] = '0.0 °C / 44.4 °C'
        record = self.record()
        self.assertEqual(record.telemetry.fan_rpm, [0, 4860, 0, 5100])
        self.assertEqual(record.telemetry.temperatures_c[:2], [0, 44.4])
        board['fanspeed'] = 'network failure 500'
        board['temp'] = 'unknown sensor 200'
        record = self.record()
        self.assertEqual(record.telemetry.fan_rpm, [])
        self.assertEqual(len(record.telemetry.temperatures_c), 6)

    def test_missing_or_partial_board_array_is_not_partial_hashrate(self):
        for rows in ([], None, [self.data['goldshell_devs']['data'][0], 'bad'], {}):
            with self.subTest(rows=rows):
                self.data['goldshell_devs']['data'] = rows
                record = self.record()
                self.assertIsNone(record.telemetry.rate)
                self.assertEqual(record.display['Status'], 'Unknown')

    def test_board_fault_is_not_sleep(self):
        for board in self.data['goldshell_devs']['data']:
            board.update(hashrate=0, minerstatus=2)
        record = self.record()
        self.assertEqual(record.display['Status'], 'Error')
        self.assertEqual(record.display['Error'], 'BOARD STATUS')
        self.assertIn('CPB 0: 2', record.display['ErrorDetails'])

    def test_led_state_is_read_without_enabling_control(self):
        for value in (True, False, None, 1):
            with self.subTest(value=value):
                self.data['goldshell_setting']['ledcontrol'] = value
                record = self.record()
                self.assertIs(record.to_legacy()['IdentifyEnabled'], value if type(value) is bool else None)
                self.assertTrue(all(state == 'unsupported' for state in record.capabilities.values()))

    def test_discovery_filters_cached_poll_and_algorithm_refresh(self):
        factory = FakeFactory(self.data)
        service = self.service(factory)
        record = service.poll('192.0.2.13', target_makes=['Goldshell'])
        self.assertEqual(record.display['Model'], 'Goldshell-HS5')
        self.assertFalse(record.telemetry.stale)
        self.assertIs(record.to_legacy()['IdentifyEnabled'], False)
        factory.data['goldshell_setting']['ledcontrol'] = True
        factory.data['goldshell_algo']['algo_select'] = 0
        record = service.poll('192.0.2.13', target_makes=['Goldshell'])
        self.assertEqual(record.display['Algo'], 'Blake2B+SHA3')
        self.assertIs(record.to_legacy()['IdentifyEnabled'], True)
        self.assertEqual(sum(key == 'goldshell_status' for _, key in factory.calls), 1)
        self.assertIsNone(service.poll('192.0.2.13', target_makes=['Bitmain']))
        self.assertFalse(factory.writes)

    def test_active_pool_and_worker_changes_do_not_change_identity(self):
        self.data['goldshell_pools'] = [
            {'url': 'stratum+tcp://unused.example', 'user': 'unused', 'active': False},
            {'url': 'stratum+tcp://active.example', 'user': 'demo.worker', 'pass': 'demo', 'active': True}]
        before = self.record()
        self.assertEqual(before.display['Worker'], 'demo.worker')
        self.assertEqual(before.display['Pool'], 'stratum+tcp://active.example')
        self.data['goldshell_pools'][1].update(user='new.worker', Type='Antminer S21', firmware_version='wrong')
        after = self.record()
        self.assertEqual(before.identity.fingerprint, after.identity.fingerprint)
        self.assertEqual(ProfileRegistry().resolve(self.data).id, 'goldshell.mcb')
        self.assertIsNone(ProfileRegistry().resolve({'goldshell_pools': self.data['goldshell_pools']}))

    def test_offline_rescan_does_not_present_cached_rates_as_current(self):
        factory = FakeFactory(self.data)
        service = self.service(factory)
        service.poll('192.0.2.13')
        factory.data.clear()
        record = service.poll('192.0.2.13')
        self.assertTrue(record.telemetry.stale)
        self.assertEqual(record.to_legacy()['Real'], '—')
        self.assertEqual(record.to_legacy()['Fan'], '—')
        self.assertIsNone(record.to_legacy()['IdentifyEnabled'])

    def test_plain_web_page_or_other_vendor_is_not_goldshell(self):
        registry = ProfileRegistry()
        for data in ({'web_status': '<title>cloud-box</title>'},
                     {'goldshell_status': {'model': 'Router', 'firmware': '2.2.1'}},
                     {'goldshell_status': {'model': 'Goldshell-HS5'}},
                     {'goldshell_status': []}):
            self.assertIsNone(registry.resolve(data))

    def test_diagnostic_allowlist_accepts_reads_and_rejects_writes(self):
        factory = FakeFactory(self.data)
        capture = Capture(['192.0.2.13'])
        with RecordingTransport('192.0.2.13', Operation(), None, capture, factory) as transport:
            for query in self.profile.queries:
                transport.http_json(query[2])
            for path in ('/mcb/restart', '/mcb/facrst', '/mcb/cgminer?cgminercmd=restart'):
                with self.assertRaises(ValueError):
                    transport.http_json(path)
            with self.assertRaises(ValueError):
                transport.http_json('/mcb/setting', 'PUT')
        self.assertFalse(factory.writes)


if __name__ == '__main__':
    unittest.main()
