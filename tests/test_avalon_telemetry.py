"""Sleep state must win over decaying CGMiner rolling hash-rate averages."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from miner_scanner.drivers import make_record
from miner_scanner.profiles import ProfileRegistry
from miner_scanner.avalon_compatibility import display_status


class AvalonTelemetryTests(unittest.TestCase):
    def setUp(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/avalon_1346_fms.json').read_text())
        self.data = {'version': fixture['version'], 'stats': deepcopy(fixture['after_sleep']),
                     'summary': {'SUMMARY': [{'Elapsed': 1167, 'MHS 1m': 1102.8, 'MHS av': 22622399.47}]}}
        self.data['stats']['STATS'][0]['MM ID0'] = (
            'Ver[1346-110-24041001_08b0955_0196aba] Elapsed[1168] SoftOFF[5] '
            'SYSTEMSTATU[Work: In Idle, Hash Board: 3 ] GHSspd[0.00] GHSmm[0.00] '
            'GHSavg[22622.40] PS[0 1198 0 0 0 1393 11]')
        self.profile = ProfileRegistry().by_id['canaan.avalon']

    def record(self):
        return make_record(self.profile, '192.0.2.1', self.data)

    def replace(self, old, new):
        row = self.data['stats']['STATS'][0]
        row['MM ID0'] = row['MM ID0'].replace(old, new)

    def test_observed_sleep_with_positive_minute_and_lifetime_averages(self):
        record = self.record()
        self.assertEqual(record.display['Status'], 'Sleep')
        self.assertEqual(record.telemetry.mining_state, 'stopped')
        self.assertEqual(record.telemetry.rate, 0)
        self.assertEqual(record.telemetry.average_rate, 22622399470000)
        self.assertEqual(record.telemetry.uptime_seconds, 1168)
        self.assertEqual(record.to_legacy()['Real'], '0.00 TH/s')

    def test_shutdown_before_board_power_is_off_is_a_transition(self):
        self.replace('PS[0 1198 0 0 0 1393 11]', 'PS[0 1199 1393 223 3107 1393 3334]')
        record = self.record()
        self.assertEqual(record.display['Status'], 'Stopping')
        self.assertEqual(record.telemetry.mining_state, 'stopping')

    def test_shutdown_with_residual_current_rate_is_not_running(self):
        self.replace('GHSspd[0.00]', 'GHSspd[10.00]')
        record = self.record()
        self.assertEqual(record.display['Status'], 'Stopping')
        self.assertEqual(record.telemetry.mining_state, 'stopping')
        self.assertEqual(record.telemetry.rate, 10000000000)

    def test_startup_does_not_reuse_old_minute_hashrate(self):
        self.replace('SoftOFF[5]', 'SoftOFF[0]')
        self.replace('Work: In Idle,', 'Work: In Work,')
        self.replace('PS[0 1198 0 0 0 1393 11]', 'PS[0 1199 1392 34 776 1393 837]')
        record = self.record()
        self.assertEqual(record.display['Status'], 'WaitWork')
        self.assertEqual(record.telemetry.mining_state, 'starting')
        self.assertEqual(record.telemetry.rate, 0)

    def test_active_device_prefers_current_module_hashrate(self):
        self.replace('SoftOFF[5]', 'SoftOFF[0]')
        self.replace('Work: In Idle,', 'Work: In Work,')
        self.replace('GHSspd[0.00]', 'GHSspd[110778.38]')
        self.replace('PS[0 1198 0 0 0 1393 11]', 'PS[0 1199 1391 225 3126 1393 3348]')
        record = self.record()
        self.assertEqual(record.display['Status'], 'Running')
        self.assertEqual(record.telemetry.mining_state, 'running')
        self.assertAlmostEqual(record.telemetry.rate, 110778380000000)

    def test_boot_softoff_6_does_not_imply_sleep_or_running(self):
        self.replace('SoftOFF[5]', 'SoftOFF[6]')
        record = self.record()
        self.assertEqual(record.display['Status'], 'Unknown')
        self.assertEqual(record.telemetry.mining_state, 'unknown')

    def test_other_series_and_incomplete_power_data_do_not_inherit_sleep(self):
        self.data['version']['VERSION'][0]['MODEL'] = 'OTHER'
        self.assertIsNone(display_status(self.data))
        self.data['version']['VERSION'][0]['MODEL'] = '1346-110'
        self.replace('PS[0 1198 0 0 0 1393 11]', '')
        self.assertIsNone(display_status(self.data))


if __name__ == '__main__':
    unittest.main()
