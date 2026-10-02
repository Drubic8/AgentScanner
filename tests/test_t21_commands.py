"""T21 Jan 2025 command contracts, tested without real ASIC access."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from miner_scanner.commands import execute_command
from miner_scanner.models import Credentials
from miner_scanner.repository import DeviceRepository
from miner_scanner.runtime import Operation
from miner_scanner.service import ScannerService
from tests.fakes import FakeFactory, FakeTransport
from tests.test_stock_mode_timeout import device


class T21Transport(FakeTransport):
    def http(self, path, method='GET', *, payload=None, **kwargs):
        response = super().http(path, method, payload=payload, **kwargs)
        if method == 'POST' and path.endswith('set_miner_conf.cgi'):
            self.factory.data['config']['bitmain-work-mode'] = str(payload['miner-mode'])
            return 200, b'{"stats":"success","code":"M000"}'
        if method == 'POST' and path.endswith('blink.cgi'):
            self.factory.data['/cgi-bin/get_blink_status.cgi'] = {'blink': payload['blink']}
            return 200, b'{"code":"B000"}' if payload['blink'] else b'{"code":"B100"}'
        return response


class T21CommandTests(unittest.TestCase):
    def setUp(self):
        data = device(version='Thu Jan 16 11:00:25 CST 2025')
        data['config'].update({'bitmain-work-mode': '0', 'bitmain-user-ip-cat': '0'})
        self.factory = FakeFactory(data)
        self.factory.apply_mode = False
        repo = DeviceRepository(':memory:')
        self.addCleanup(repo.close)
        self.service = ScannerService(repository=repo, transport_factory=lambda *args: T21Transport(self.factory, *args))
        self.service.set_default_credentials(Credentials('root', 'root'))
        self.record = self.service.poll('192.0.2.27')

    def test_sequence_preserves_settings_and_writes_expected_modes(self):
        self.assertEqual(self.record.identity.profile_id, 'bitmain.stock.t21.20250116')
        baseline = deepcopy(self.factory.data['config'])
        with patch.object(Operation, 'pause', lambda *_: None):
            for action in ('led_on', 'led_off', 'sleep', 'normal', 'hem', 'normal_power', 'reboot'):
                result = execute_command(self.service, '192.0.2.27', action)
                self.assertEqual(result.status, 'unconfirmed' if action == 'reboot' else 'succeeded', action)
        self.assertEqual(len(self.factory.writes), 7)
        configs = [payload for path, _, payload in self.factory.writes if path.endswith('set_miner_conf.cgi')]
        self.assertEqual([p['miner-mode'] for p in configs], [1, 0, 2, 0])
        for payload in configs:
            self.assertEqual({k:v for k,v in payload.items() if k != 'miner-mode'},
                             {k:v for k,v in baseline.items() if k != 'bitmain-work-mode'})
        self.assertEqual(self.factory.writes[-1], ('/cgi-bin/reboot.cgi', 'GET', None))

    def test_low_is_not_sent_to_t21(self):
        result = execute_command(self.service, '192.0.2.27', 'low', allow_unverified=True)
        self.assertEqual(result.status, 'unsupported')
        self.assertFalse(self.factory.writes)

    def test_missing_required_field_blocks_config_write(self):
        del self.factory.data['config']['bitmain-user-ip-cat']
        result = execute_command(self.service, '192.0.2.27', 'hem', allow_unverified=True)
        self.assertEqual(result.status, 'failed')
        self.assertFalse(self.factory.writes)

    def test_other_firmware_does_not_inherit_verified_controls(self):
        self.factory.data['system']['system_filesystem_version'] = 'other build'
        self.service.poll('192.0.2.27', force_identify=True)
        result = execute_command(self.service, '192.0.2.27', 'reboot')
        self.assertEqual(result.status, 'unsupported')
        self.assertFalse(self.factory.writes)
