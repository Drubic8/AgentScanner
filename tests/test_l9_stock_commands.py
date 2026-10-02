"""L9 stock UI writes different names from those returned by its config API."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from miner_scanner.commands import execute_command, _http_accept
from miner_scanner.models import Credentials
from miner_scanner.profiles import ProfileRegistry
from miner_scanner.repository import DeviceRepository
from miner_scanner.runtime import Operation
from miner_scanner.service import ScannerService
from tests.fakes import FakeFactory, FakeTransport, stock


def l9():
    data = stock()
    data['version']['VERSION'][0] = {'Type': 'Antminer L9', 'API': '3.1', 'Miner': '86.48-2.0.0'}
    data['stats']['STATS'][0]['Type'] = 'Antminer L9'
    data['system'] = {'minertype': 'Antminer L9', 'system_filesystem_version': 'Mon Feb 10 10:01:06 CST 2025'}
    data['config'].update({'bitmain-work-mode': '1', 'bitmain-freq-level': '100',
                           'bitmain-fan-ctrl': False, 'bitmain-fan-pwm': '100', 'algo': 'scrypt'})
    return data


class L9Transport(FakeTransport):
    def http(self, path, method='GET', *, payload=None, **kwargs):
        result = super().http(path, method, payload=payload, **kwargs)
        if method == 'POST' and path.endswith('set_miner_conf.cgi') and self.factory.apply_readback:
            self.factory.data['config']['bitmain-work-mode'] = str(payload['miner-mode'])
        if method == 'POST' and path.endswith('blink.cgi'):
            if self.factory.apply_readback:
                self.factory.data['/cgi-bin/get_blink_status.cgi'] = {'blink': payload['blink']}
            if payload['blink'] is False:
                return 200, b'{"code":"B100"}'
        return result


class L9StockCommands(unittest.TestCase):
    def setUp(self):
        self.factory = FakeFactory(l9())
        self.factory.apply_mode = False
        self.factory.apply_readback = True
        repository = DeviceRepository(':memory:')
        self.addCleanup(repository.close)
        self.service = ScannerService(repository=repository,
            transport_factory=lambda *args: L9Transport(self.factory, *args))
        self.service.set_default_credentials(Credentials('root', 'root'))
        self.service.poll('192.0.2.9')

    def start(self, **kwargs):
        with patch.object(Operation, 'pause', lambda *_: None):
            return execute_command(self.service, '192.0.2.9', 'normal', **kwargs)

    def test_wake_maps_ui_fields_and_preserves_pool_and_fan_settings(self):
        before = deepcopy(self.factory.data['config'])
        result = self.start(command_id='wake-once')
        self.assertEqual(result.status, 'succeeded')
        self.assertEqual(self.factory.writes, [('/cgi-bin/set_miner_conf.cgi', 'POST', {
            'pools': before['pools'], 'bitmain-fan-ctrl': False, 'bitmain-fan-pwm': '100',
            'freq-level': '100', 'miner-mode': 0})])
        self.assertEqual(self.start(command_id='wake-once'), result)
        self.assertEqual(len(self.factory.writes), 1)

    def test_wakeup_after_sleep_telemetry_version_disappears(self):
        # L9 exposes a program version in stats and a different system build.
        self.factory.data['stats']['STATS'].append({'miner_version': '86.48-2.0.0'})
        before = self.service.poll('192.0.2.9', force_identify=True)
        self.assertEqual(before.identity.firmware_version, 'Mon Feb 10 10:01:06 CST 2025')
        del self.factory.data['stats']['STATS'][1]
        result = self.start(device_id=before.identity.device_id)
        self.assertEqual(result.status, 'succeeded')
        after = self.service.get_record('192.0.2.9')
        self.assertEqual(before.identity.fingerprint, after.identity.fingerprint)
        self.assertEqual(before.identity.device_id, after.identity.device_id)
        self.assertEqual(len(self.factory.writes), 1)

    def test_wakeup_after_stats_endpoint_temporarily_unavailable(self):
        before = self.service.get_record('192.0.2.9')
        self.factory.data['stats'] = TimeoutError()
        result = self.start(device_id=before.identity.device_id)
        self.assertEqual(result.status, 'succeeded')
        self.assertEqual(len(self.factory.writes), 1)

    def test_system_build_change_still_blocks_write(self):
        before = self.service.get_record('192.0.2.9')
        self.factory.data['system']['system_filesystem_version'] = 'another image build'
        result = self.start(device_id=before.identity.device_id)
        self.assertEqual(result.status, 'skipped')
        self.assertIn('версия прошивки', result.message)
        self.assertFalse(self.factory.writes)

    def test_outdated_selected_id_is_reported_and_does_not_bypass_check(self):
        result = self.start(device_id='outdated-selected-id')
        self.assertEqual(result.status, 'skipped')
        self.assertIn('идентификатор выбранной строки', result.message)
        self.assertFalse(self.factory.writes)

    def test_missing_or_masked_config_sends_no_write(self):
        for invalid in ('missing', 'masked'):
            self.factory.data = l9()
            if invalid == 'missing':
                del self.factory.data['config']['bitmain-freq-level']
            else:
                self.factory.data['config']['pools'][0]['pass'] = '*****'
            self.assertEqual(self.start().status, 'failed')
            self.assertFalse(self.factory.writes)

    def test_accepted_without_mode_change_is_unconfirmed(self):
        self.factory.apply_readback = False
        self.assertEqual(self.start().status, 'unconfirmed')
        self.assertEqual(len(self.factory.writes), 1)

    def test_different_build_does_not_enable_rule(self):
        registry = ProfileRegistry()
        data = l9()
        self.assertEqual(registry.resolve(data).id, 'bitmain.stock.l9.20250210')
        data['system']['system_filesystem_version'] = 'other build'
        self.assertEqual(registry.resolve(data).id, 'bitmain.stock')

    def test_success_code_does_not_override_explicit_failure(self):
        from unittest.mock import Mock
        transport = Mock()
        transport.http.return_value = (200, b'{"stats":"error","code":"B000"}')
        self.assertFalse(_http_accept(transport, '/cgi-bin/set_miner_conf.cgi'))

    def test_write_timeout_checks_readback_without_replaying(self):
        original = L9Transport.http
        def timeout_after_apply(transport, path, method='GET', **kwargs):
            response = original(transport, path, method, **kwargs)
            if method == 'POST':
                raise TimeoutError()
            return response
        with patch.object(L9Transport, 'http', timeout_after_apply):
            result = self.start()
        self.assertEqual(result.status, 'succeeded')
        self.assertFalse(result.accepted_by_api)
        self.assertEqual(len(self.factory.writes), 1)

    def test_write_timeout_without_state_change_remains_unconfirmed(self):
        self.factory.write_error = TimeoutError()
        result = self.start()
        self.assertEqual(result.status, 'unconfirmed')
        self.assertFalse(result.accepted_by_api)
        self.assertEqual(len(self.factory.writes), 1)

    def test_led_on_off_readback_and_no_replay(self):
        with patch.object(Operation, 'pause', lambda *_: None):
            on = execute_command(self.service, '192.0.2.9', 'led_on',
                                 command_id='led-on-once')
            self.assertEqual(on.status, 'succeeded')
            again = execute_command(self.service, '192.0.2.9', 'led_on',
                                    command_id='led-on-once')
            self.assertEqual(again, on)
            off = execute_command(self.service, '192.0.2.9', 'led_off')
        self.assertEqual(off.status, 'succeeded')
        self.assertEqual(self.factory.writes, [('/cgi-bin/blink.cgi', 'POST', {'blink': True}),
                                               ('/cgi-bin/blink.cgi', 'POST', {'blink': False})])

    def test_led_off_needs_explicit_boolean_false(self):
        self.factory.apply_readback = False
        for response in ({}, {'blink': 'false'}, {'blink': 0}, {'blink': True}):
            self.factory.data['/cgi-bin/get_blink_status.cgi'] = response
            with patch.object(Operation, 'pause', lambda *_: None):
                result = execute_command(self.service, '192.0.2.9', 'led_off')
            self.assertEqual(result.status, 'unconfirmed')

    def test_unknown_build_without_led_api_is_not_enabled(self):
        self.factory.data['system']['system_filesystem_version'] = 'other build'
        self.service.poll('192.0.2.9', force_identify=True)
        for action in ('led_on', 'led_off'):
            result = execute_command(self.service, '192.0.2.9', action)
            self.assertEqual(result.status, 'unsupported')
        self.assertFalse(self.factory.writes)

    def test_b100_is_not_a_global_success_code(self):
        from unittest.mock import Mock
        transport = Mock()
        transport.http.return_value = (200, b'{"code":"B100"}')
        self.assertFalse(_http_accept(transport, '/cgi-bin/blink.cgi'))
