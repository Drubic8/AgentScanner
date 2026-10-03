"""PitBit.78 S21 acceptance regressions; synthetic transports only."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from miner_scanner.commands import execute_command
from miner_scanner.models import Credentials
from miner_scanner.repository import DeviceRepository
from miner_scanner.runtime import Operation
from miner_scanner.service import ScannerService
from tests.fakes import FakeFactory, FakeTransport


def device():
    return {
        'version': {'VERSION': [{'Type': 'Antminer S21_i', 'API': '3.1'}]},
        'stats': {'STATS': [{'Type': 'Antminer S21_i'},
                            {'miner_version': 'incm.C074', 'GHS 5s': 205000, 'Elapsed': 600}]},
        'system': {'minertype': 'Antminer S21',
                   'system_filesystem_version': 'Fri Feb 2 17:03:40 CST 2024',
                   'macaddr': '02:00:00:00:00:03'},
        'config': {'bitmain-work-mode': '0', 'bitmain-freq': '200',
                   'working_voltage': '14210', 'target-temp': '63',
                   'pools': [{'url': 'stratum+tcp://pool.invalid:3333', 'user': 'demo.1', 'pass': 'x'}]},
        '/cgi-bin/get_blink_status.cgi': {'blink': False},
    }


class PitbitTransport(FakeTransport):
    def http(self, path, method='GET', *, payload=None, **kwargs):
        response = super().http(path, method, payload=payload, **kwargs)
        if path.endswith('set_miner_conf.cgi') and method == 'POST':
            return 200, self.factory.mode_reply
        if path.endswith('blink.cgi') and method == 'POST':
            self.factory.data['/cgi-bin/get_blink_status.cgi'] = {'blink': payload['blink']}
        return response


class PitbitCommandTests(unittest.TestCase):
    def setUp(self):
        self.factory = FakeFactory(device())
        self.factory.mode_reply = b'{"stats":"success","code":"M000"}'
        repo = DeviceRepository(':memory:')
        self.addCleanup(repo.close)
        self.service = ScannerService(repository=repo,
            transport_factory=lambda *args: PitbitTransport(self.factory, *args))
        self.service.set_default_credentials(Credentials('root', 'root'))
        self.record = self.service.poll('192.0.2.3')

    def test_verified_sequence_writes_only_mode_and_preserves_configuration(self):
        self.assertEqual(self.record.identity.profile_id, 'bitmain.pitbit.s21.c074.20240202')
        baseline = deepcopy(self.factory.data['config'])
        for action in ('led_on', 'led_off', 'sleep', 'wakeup'):
            self.assertEqual(execute_command(self.service, '192.0.2.3', action).status, 'succeeded', action)
        self.assertEqual(self.factory.data['config'], baseline)
        self.assertEqual([payload for path, _, payload in self.factory.writes if path.endswith('set_miner_conf.cgi')],
                         [{'bitmain-work-mode': '1'}, {'bitmain-work-mode': '0'}])
        self.assertFalse(self.factory.data['/cgi-bin/get_blink_status.cgi']['blink'])

    def test_rejection_is_failed_and_write_is_not_retried(self):
        self.factory.apply_mode = False
        self.factory.mode_reply = b'{"stats":"error","code":"M001"}'
        self.assertEqual(execute_command(self.service, '192.0.2.3', 'sleep').status, 'failed')
        self.assertEqual(len(self.factory.writes), 1)
        self.assertEqual(self.factory.data['config']['bitmain-work-mode'], '0')

    def test_success_reply_without_readback_is_unconfirmed(self):
        self.factory.apply_mode = False
        with patch.object(Operation, 'pause', lambda *_: None):
            self.assertEqual(execute_command(self.service, '192.0.2.3', 'sleep').status, 'unconfirmed')
        self.assertEqual(len(self.factory.writes), 1)

    def test_missing_mode_does_not_write(self):
        del self.factory.data['config']['bitmain-work-mode']
        self.assertEqual(execute_command(self.service, '192.0.2.3', 'sleep').status, 'failed')
        self.assertFalse(self.factory.writes)

    def test_unknown_engine_build_api_or_model_cannot_inherit_verified_modes(self):
        changes = [('stats', 'STATS', 1, 'miner_version', 'incm.future'),
                   ('system', 'system_filesystem_version', 'other build'),
                   ('version', 'VERSION', 0, 'API', '9.0'),
                   ('version', 'VERSION', 0, 'Type', 'Antminer S19')]
        for change in changes:
            with self.subTest(change=change):
                self.factory.data = device()
                item = self.factory.data
                for key in change[:-2]:
                    item = item[key]
                item[change[-2]] = change[-1]
                # Test a fresh GUI scan, not a retained selection from another build.
                record = self.service.poll('192.0.2.3', force_identify=True)
                self.assertEqual(record.identity.profile_id, 'bitmain.pitbit')
                self.assertEqual(execute_command(self.service, '192.0.2.3', 'sleep').status, 'unsupported')
                self.assertFalse(self.factory.writes)

    def test_power_modes_remain_unavailable(self):
        for action in ('low', 'hem', 'normal_power'):
            self.assertEqual(execute_command(self.service, '192.0.2.3', action).status, 'unsupported')
        self.assertFalse(self.factory.writes)

    def test_reboot_writes_once_without_claiming_completion_from_http(self):
        result = execute_command(self.service, '192.0.2.3', 'reboot')
        self.assertEqual(result.status, 'unconfirmed')
        self.assertEqual(self.factory.writes, [('/cgi-bin/reboot.cgi', 'GET', None)])

    def test_sleep_transition_keeps_residual_hashrate_without_showing_running(self):
        self.factory.data['config']['bitmain-work-mode'] = '1'
        record = self.service.poll('192.0.2.3', force_identify=True)
        self.assertEqual(record.telemetry.mining_state, 'stopping')
        self.assertGreater(record.telemetry.rate, 0)
        self.factory.data['stats']['STATS'][1]['GHS 5s'] = 0
        self.factory.data['stats']['STATS'][1]['rate_30m'] = 180000
        record = self.service.poll('192.0.2.3', force_identify=True)
        self.assertEqual(record.telemetry.mining_state, 'stopped')
        self.assertEqual(record.telemetry.rate, 0)
