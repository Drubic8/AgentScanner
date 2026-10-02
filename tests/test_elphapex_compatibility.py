"""Exercise discovery and the public dispatcher, using no real devices."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from miner_scanner import elphapex_compatibility as compat
from miner_scanner.commands import execute_command
from miner_scanner.repository import DeviceRepository
from miner_scanner.runtime import Operation, DeadlineExceeded
from miner_scanner.service import ScannerService
from tests.fakes import FakeFactory, FakeTransport


class ElphapexTransport(FakeTransport):
    def http(self, path, method='GET', *, payload=None, **kwargs):
        if method == 'GET' and path in self.factory.web:
            self.factory.calls.append((self.ip, path))
            return 200, self.factory.web[path]
        if path == '/cgi-bin/luci/ftm_ledtest.cgi' and method == 'POST':
            self.factory.writes.append((path, method, deepcopy(payload)))
            if self.factory.apply_mode:
                self.factory.data['/cgi-bin/luci/get_blink_status.cgi']['blink'] = payload['leds_blue'] == 1
            return 200, b'{"stats":"success","code":"M000"}'
        if path == '/cgi-bin/luci/setworkmode.cgi' and method == 'POST':
            self.factory.writes.append((path, method, deepcopy(payload)))
            self.factory.data['elphapex_config']['fc-work-mode'] = payload['workmode']
            return 200, b'{"stats":"success","code":"M000"}'
        return super().http(path, method, payload=payload, **kwargs)


class ElphapexCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.object(Operation, 'pause', lambda *_: None).start()
        self.factory = FakeFactory({
            'elphapex_stats': {'INFO': {'type': 'DG1', 'miner_version': 'DG1_SW_V1.0.4', 'api_version': '1.0.0'},
                              'STATS': [{'elapsed': 120, 'rate_5s': 11000, 'rate_avg': 11000}]},
            'elphapex_config': {'fc-work-mode': '0', 'pools': []},
            '/cgi-bin/luci/get_blink_status.cgi': {'blink': False},
        })
        self.factory.web = {}
        repo = DeviceRepository(':memory:')
        self.addCleanup(repo.close)
        self.service = ScannerService(repository=repo,
            transport_factory=lambda *a: ElphapexTransport(self.factory, *a))
        self.record = self.service.poll('192.0.2.1')

    def test_dispatcher_can_set_led_and_confirms_luci_readback(self):
        self.assertEqual(self.record.identity.profile_id, 'elphapex.luci')
        self.assertEqual(self.record.capabilities['identify_on'], 'supported')
        self.assertEqual(self.record.display['ControlCompatibility']['compatible_commands'],
                         ['identify_off', 'identify_on', 'mining_start', 'mining_stop', 'reboot'])
        baseline = deepcopy(self.factory.data['elphapex_config'])
        self.assertFalse(self.factory.writes)
        for action, enabled in [('led_on', True), ('led_off', False)]:
            self.assertEqual(execute_command(self.service, '192.0.2.1', action).status, 'succeeded')
            self.assertEqual(self.factory.writes[-1], ('/cgi-bin/luci/ftm_ledtest.cgi', 'POST',
                {'leds_blue': int(enabled), 'leds_red': 0, 'leds_flash': int(enabled), 'leds_time': 0}))
            self.assertIs(self.factory.data['/cgi-bin/luci/get_blink_status.cgi']['blink'], enabled)
        self.assertEqual(self.factory.data['elphapex_config'], baseline)
        self.assertEqual(len(self.factory.writes), 2)

    def test_luci_schema_is_rechecked_before_write(self):
        self.factory.data['/cgi-bin/luci/get_blink_status.cgi'] = {'enabled': True}
        self.assertEqual(execute_command(self.service, '192.0.2.1', 'led_on', allow_unverified=True).status,
                         'unsupported')
        self.assertFalse(self.factory.writes)

    def test_api_version_is_information_not_a_led_version_lock(self):
        self.factory.data['elphapex_stats']['INFO']['api_version'] = '2.0.0'
        record = self.service.poll('192.0.2.1', force_identify=True)
        self.assertEqual(record.capabilities['identify_on'], 'supported')
        self.assertEqual(execute_command(self.service, '192.0.2.1', 'led_on').status, 'succeeded')

    def test_dg1plus_has_same_led_api_across_builds(self):
        for version in ('DG1+_SW_V1.0.4', 'different build'):
            self.factory.data['elphapex_stats']['INFO'].update(type='DG1+', miner_version=version)
            record = self.service.poll('192.0.2.1', force_identify=True)
            self.assertEqual(record.identity.model, 'DG1+')
            self.assertEqual(record.capabilities['identify_on'], 'supported')
            for action in ('led_on', 'led_off'):
                self.assertEqual(execute_command(self.service, '192.0.2.1', action).status, 'succeeded')

    def test_firmware_date_does_not_replace_luci_api_identity(self):
        evidence = compat.probe(ElphapexTransport(self.factory, '192.0.2.1', Operation()))
        self.record.identity.firmware_version = 'different build with the same API'
        self.assertEqual(set(compat.resolve(self.record, evidence)['rules']), {'identify_on', 'identify_off', 'reboot'})

    def test_sleep_wake_reboot_use_luci_contract_and_preserve_configuration(self):
        self.factory.data['elphapex_stats']['INFO']['type'] = 'DG1+'
        self.factory.data['elphapex_config']['fan-setting'] = 'preserve-me'
        self.service.poll('192.0.2.1', force_identify=True)
        for action, target in [('sleep', '-1000'), ('wakeup', '0')]:
            self.assertEqual(execute_command(self.service, '192.0.2.1', action).status, 'succeeded')
            self.assertEqual(self.factory.writes[-1], ('/cgi-bin/luci/setworkmode.cgi', 'POST', {'workmode': target}))
            self.assertEqual(self.factory.data['elphapex_config']['fan-setting'], 'preserve-me')
        self.assertEqual(execute_command(self.service, '192.0.2.1', 'reboot').status, 'unconfirmed')
        self.assertEqual(self.factory.writes[-1], ('/cgi-bin/luci/reboot.cgi', 'GET', None))

    def test_unknown_luci_mode_schema_does_not_authorize_power_commands(self):
        for value in (None, True, 'different mode'):
            self.factory.data['elphapex_config']['fc-work-mode'] = value
            self.assertEqual(execute_command(self.service, '192.0.2.1', 'sleep').status, 'unsupported')
        self.assertFalse(self.factory.writes)

    def test_boolean_status_is_required(self):
        for value in ('false', 0, None):
            self.factory.data['/cgi-bin/luci/get_blink_status.cgi']['blink'] = value
            self.assertEqual(execute_command(self.service, '192.0.2.1', 'led_on').status, 'unsupported')
        self.assertFalse(self.factory.writes)

    def test_success_reply_without_target_state_is_unconfirmed(self):
        self.factory.apply_mode = False
        self.assertEqual(execute_command(self.service, '192.0.2.1', 'led_on').status, 'unconfirmed')
        self.assertEqual(len(self.factory.writes), 1)

    def test_contract_is_not_granted_to_another_model_or_vendor(self):
        evidence = compat.probe(ElphapexTransport(self.factory, '192.0.2.1', Operation()))
        for make, model, firmware in [('Elphapex', 'DG2', 'Stock'), ('Bitmain', 'DG1', 'Stock'),
                                      ('Elphapex', 'DG1', 'Custom')]:
            self.record.identity.make = make
            self.record.identity.model = model
            self.record.identity.firmware = firmware
            self.assertFalse(compat.resolve(self.record, evidence)['rules'])

    def test_cache_and_idempotency_do_not_repeat_discovery_or_write(self):
        before = self.factory.calls.count(('192.0.2.1', '/cgi-bin/luci/get_blink_status.cgi'))
        self.service.poll('192.0.2.1')
        self.assertEqual(self.factory.calls.count(('192.0.2.1', '/cgi-bin/luci/get_blink_status.cgi')), before)
        result = execute_command(self.service, '192.0.2.1', 'led_on', command_id='elphapex-once')
        self.assertEqual(execute_command(self.service, '192.0.2.1', 'led_on', command_id='elphapex-once'), result)
        self.assertEqual(len(self.factory.writes), 1)
        # One fresh contract probe and one readback, with no repeated write.
        self.assertEqual(self.factory.calls.count(('192.0.2.1', '/cgi-bin/luci/get_blink_status.cgi')), before + 2)

    def test_probe_deadline_retains_telemetry(self):
        self.factory.data['/cgi-bin/luci/get_blink_status.cgi'] = DeadlineExceeded()
        current = self.service.poll('192.0.2.1', force_identify=True)
        self.assertFalse(current.telemetry.stale)
        self.assertEqual(current.display['ControlCompatibility']['compatible_commands'], [])
