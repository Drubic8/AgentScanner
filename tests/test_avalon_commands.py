"""Documented LED protocol and sanitized Avalon 1346 response shapes."""
import unittest
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from miner_scanner import avalon_compatibility as compat
from miner_scanner.commands import execute_command
from miner_scanner.repository import DeviceRepository
from miner_scanner.runtime import Operation
from miner_scanner.service import ScannerService
from tests.fakes import FakeFactory, FakeTransport


class AvalonTransport(FakeTransport):
    def text_command(self, command):
        self.factory.calls.append((self.ip, command))
        if command == 'ascset|0,led,1-255':
            if self.factory.bad_read:
                return 'STATUS=E,When=92,Code=120,Msg=Invalid command,Description=cgminer 4.11.1|'
            return f'STATUS=I,When=92,Code=118,Msg=ASC 0 set info: LED[{self.factory.led}],Description=cgminer 4.11.1|'
        self.factory.writes.append(command)
        if command in compat.POWER_COMMANDS.values():
            if getattr(self.factory, 'power_after', None) is not None:
                self.factory.data['stats'] = deepcopy(self.factory.power_after)
            if self.factory.write_error:
                raise self.factory.write_error
            return getattr(self.factory, 'power_reply', 'STATUS=S,When=92,Code=119,Msg=ASC 0 set OK,Description=cgminer 4.11.1|')
        if command not in ('ascset|0,led,1-1', 'ascset|0,led,1-0'):
            raise AssertionError('Unexpected write')
        self.factory.led = int(command[-1])
        if self.factory.write_error:
            raise self.factory.write_error
        return 'STATUS=S,When=92,Code=119,Msg=ASC 0 set OK,Description=cgminer 4.11.1|'


class AvalonCommandsTests(unittest.TestCase):
    def setUp(self):
        self.factory = FakeFactory({
            'version': {'VERSION': [{'PROD': 'AvalonMiner 1346-110', 'MODEL': '1346-110',
                                    'VERSION': '24041001_08b0955_0196aba', 'API': '3.7'}]},
            'stats': {'STATS': [{'ID': 'AVA100', 'MM Count': 1,
                                'MM ID0': 'Ver[1346-110-24041001_08b0955_0196aba] Led[0] SoftOFF[0] GHSspd[110000]'}]},
            'summary': {'SUMMARY': [{'Elapsed': 100, 'MHS 1m': 110000000}]},
        })
        self.factory.led, self.factory.bad_read = 0, False
        repo = DeviceRepository(':memory:')
        self.addCleanup(repo.close)
        self.pause = patch.object(Operation, 'pause', lambda *_: None)
        self.pause.start()
        self.addCleanup(self.pause.stop)
        self.service = ScannerService(repository=repo, transport_factory=lambda *a: AvalonTransport(self.factory, *a))
        self.record = self.service.poll('192.0.2.1')

    def test_on_off_and_toggle_are_explicit_verified_writes(self):
        self.assertEqual(self.record.capabilities['identify_on'], 'supported')
        self.assertFalse(self.factory.writes)
        for action, target in [('led_on', 1), ('led_off', 0), ('identify_toggle', 1)]:
            self.assertEqual(execute_command(self.service, '192.0.2.1', action).status, 'succeeded')
            self.assertEqual(self.factory.writes[-1], f'ascset|0,led,1-{target}')
        self.assertNotIn('mining_stop', self.record.display['ControlCompatibility']['compatible_commands'])

    def test_failed_fresh_query_blocks_write_even_with_override(self):
        self.factory.bad_read = True
        self.assertEqual(execute_command(self.service, '192.0.2.1', 'led_on', allow_unverified=True).status, 'unsupported')
        self.assertFalse(self.factory.writes)

    def test_ambiguous_module_count_blocks_write(self):
        self.factory.data['stats']['STATS'][0]['MM Count'] = 2
        self.assertEqual(execute_command(self.service, '192.0.2.1', 'led_on').status, 'unsupported')
        self.assertFalse(self.factory.writes)

    def test_lost_write_response_is_read_back_without_repeat(self):
        self.factory.write_error = TimeoutError()
        result = execute_command(self.service, '192.0.2.1', 'led_on', command_id='once')
        self.assertEqual(result.status, 'succeeded')
        self.assertFalse(result.accepted_by_api)
        self.assertEqual(execute_command(self.service, '192.0.2.1', 'led_on', command_id='once'), result)
        self.assertEqual(self.factory.writes, ['ascset|0,led,1-1'])

    def test_state_parser_rejects_errors_and_ambiguous_values(self):
        template = 'STATUS=I,When=92,Code=118,Msg=ASC 0 set info: LED[{}],Description=cgminer 4.11.1|'
        self.assertEqual(compat.led_state(template.format(0)), 0)
        self.assertEqual(compat.led_state(template.format(1)), 1)
        for response in (template.format(2), template.format('1 0'), template.format('true'),
                         template.format(1).replace('STATUS=I', 'STATUS=E'),
                         template.format(1).replace('Code=118', 'Code=119'),
                         template.format(1) + template.format(0), None):
            self.assertIsNone(compat.led_state(response))


class AvalonPowerTests(unittest.TestCase):
    def setUp(self):
        self.capture = json.loads((Path(__file__).parent / 'fixtures/avalon_1346_fms.json').read_text())
        self.running = {'STATS': [{'MM Count': 1, 'MM ID0':
            'Ver[1346-110-24041001_08b0955_0196aba] Elapsed[370] '
            'SYSTEMSTATU[Work: In Work, Hash Board: 3 ] SoftOFF[0] '
            'GHSspd[110778.38] PS[0 1199 1391 225 3126 1393 3348]'}]}
        self.factory = FakeFactory({'version': self.capture['version'], 'stats': self.running})
        self.factory.led, self.factory.bad_read = 0, False
        repo = DeviceRepository(':memory:')
        self.addCleanup(repo.close)
        pause = patch.object(Operation, 'pause', lambda *_: None)
        pause.start()
        self.addCleanup(pause.stop)
        self.service = ScannerService(repository=repo, transport_factory=lambda *a: AvalonTransport(self.factory, *a))
        self.record = self.service.poll('192.0.2.1')

    def test_capture_contract_and_stop_transition(self):
        self.assertEqual(self.record.identity.firmware_version, '24041001_08b0955_0196aba')
        self.assertEqual(self.record.capabilities['mining_stop'], 'supported')
        self.assertFalse(self.factory.writes)
        self.factory.power_after = self.capture['after_sleep']
        self.factory.power_reply = self.capture['sleep']['response']
        result = execute_command(self.service, '192.0.2.1', 'sleep')
        self.assertEqual(result.status, 'succeeded')
        self.assertTrue(result.accepted_by_api)
        self.assertEqual(self.factory.writes, [self.capture['sleep']['request']])

    def test_observed_residual_voltage_with_zero_current_and_power(self):
        self.factory.power_after = deepcopy(self.capture['after_sleep'])
        row = self.factory.power_after['STATS'][0]
        row['MM ID0'] = row['MM ID0'].replace('PS[0 1197 0 0 0 1393 11]',
                                            'PS[0 1199 1 0 0 1393 11]').replace('SoftOFF[6]', 'SoftOFF[5]')
        self.assertEqual(execute_command(self.service, '192.0.2.1', 'sleep').status, 'succeeded')
        for ps in ('0 1199 1 1 0 1393 11', '0 1199 1 0 1 1393 11', '0 1199 1393 0 0 1393 11'):
            data = deepcopy(self.factory.power_after)
            data['STATS'][0]['MM ID0'] = row['MM ID0'].replace('0 1199 1 0 0 1393 11', ps)
            self.assertFalse(compat.power_state(data)['off'])

    def test_wakeup_is_reboot_and_reset_uptime_verifies_restart(self):
        self.factory.data['stats'] = deepcopy(self.capture['after_sleep'])
        self.factory.power_after = deepcopy(self.running)
        result = execute_command(self.service, '192.0.2.1', 'normal')
        self.assertEqual(result.status, 'succeeded')
        self.assertEqual(self.factory.writes, [self.capture['wakeup']['request']])

    def test_idle_without_transition_is_not_success(self):
        self.factory.data['stats'] = deepcopy(self.capture['after_sleep'])
        result = execute_command(self.service, '192.0.2.1', 'sleep')
        self.assertEqual(result.status, 'unconfirmed')
        self.assertTrue(result.accepted_by_api)

    def test_acceptance_without_state_change_is_not_success(self):
        result = execute_command(self.service, '192.0.2.1', 'reboot')
        self.assertEqual(result.status, 'unconfirmed')
        self.assertTrue(result.accepted_by_api)

    def test_lost_sleep_response_is_not_retried(self):
        self.factory.power_after = self.capture['after_sleep']
        self.factory.write_error = TimeoutError()
        result = execute_command(self.service, '192.0.2.1', 'sleep', command_id='stop-once')
        self.assertEqual(result.status, 'succeeded')
        self.assertFalse(result.accepted_by_api)
        self.assertEqual(execute_command(self.service, '192.0.2.1', 'sleep', command_id='stop-once'), result)
        self.assertEqual(len(self.factory.writes), 1)

    def test_rejected_write_is_not_accepted_as_success(self):
        self.factory.power_reply = self.capture['sleep']['response'].replace('STATUS=S', 'STATUS=I')
        self.assertEqual(execute_command(self.service, '192.0.2.1', 'sleep').status, 'failed')

    def test_other_hardware_blocked_even_with_override(self):
        self.factory.data['version']['VERSION'][0]['SWTYPE'] = 'OTHER'
        result = execute_command(self.service, '192.0.2.1', 'sleep', allow_unverified=True)
        self.assertEqual(result.status, 'unsupported')
        self.assertFalse(self.factory.writes)

    def test_build_date_does_not_gate_compatible_contract(self):
        self.factory.data['version']['VERSION'][0]['VERSION'] = 'different-build'
        self.factory.data['stats']['STATS'][0]['MM ID0'] = self.running['STATS'][0]['MM ID0'].replace(
            '24041001_08b0955_0196aba', 'different-build')
        self.assertTrue(compat.power_eligible(self.factory.data))

    def test_firmware_change_invalidates_selection_before_write(self):
        self.factory.data['version']['VERSION'][0]['VERSION'] = 'different-build'
        self.assertNotEqual(execute_command(self.service, '192.0.2.1', 'sleep').status, 'succeeded')
        self.assertFalse(self.factory.writes)

    def test_partial_or_ambiguous_telemetry_is_not_power_evidence(self):
        for suffix in (' PS[0 0 0 0 0 0 0]', ' Elapsed[0]'):
            data = deepcopy(self.running)
            data['STATS'][0]['MM ID0'] += suffix
            self.assertIsNone(compat.power_state(data))
        data = deepcopy(self.running)
        data['STATS'][0]['MM ID0'] = 'SoftOFF[6] GHSspd[0]'
        self.assertIsNone(compat.power_state(data))
