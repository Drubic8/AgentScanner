"""Official RPC3 wire contract exercised without ASIC or network access."""
from contextlib import contextmanager
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from miner_scanner.commands import execute_command
from miner_scanner.models import Credentials
from miner_scanner.repository import DeviceRepository
from miner_scanner.runtime import Operation
from miner_scanner.service import ScannerService
from miner_scanner.whatsminer_compatibility import resolve
from tests.fakes import FakeFactory, FakeTransport


def info():
    return {'code': 0, 'desc': 'get.device.info', 'when': 1700000000,
            'msg': {'miner': {'type': 'M61_VL50', 'working': 'true', 'cointype': 'BTC'},
                    'system': {'api': '3.0.5', 'fwversion': '20260908.12.Rel2',
                               'apiswitch': '1', 'ledstatus': 'auto'}, 'salt': 'testsalt'}}


class RpcTransport(FakeTransport):
    @contextmanager
    def connection(self, port):
        self.factory.ports.append(port)
        yield object()

    def rpc_packet(self, sock, packet):
        if packet['cmd'] == 'get.device.info':
            response = deepcopy(self.factory.data['rpc_info'])
            if self.factory.prewrite_change:
                self.factory.prewrite_change(response)
            return response
        self.factory.writes.append(deepcopy(packet))
        if self.factory.write_error:
            raise self.factory.write_error
        if self.factory.apply_mode and self.factory.code == 0:
            msg = self.factory.data['rpc_info']['msg']
            if packet['cmd'] == 'set.miner.service':
                msg['miner']['working'] = 'false' if packet['param'] == 'stop' else 'true'
            elif packet['cmd'] == 'set.system.led':
                msg['system']['ledstatus'] = 'auto' if packet['param'] == 'auto' else self.factory.led_on_state
        return {'code': self.factory.code, 'desc': self.factory.desc or packet['cmd'], 'msg': 'ok'}


class WhatsminerCommandTests(unittest.TestCase):
    def setUp(self):
        self.factory = FakeFactory({'rpc_info': info(), 'rpc_summary':
            {'code': 0, 'msg': {'summary': {'hash-realtime': 24.07, 'hash-average': 39.48}}}})
        self.factory.ports, self.factory.code, self.factory.desc = [], 0, None
        self.factory.led_on_state = 'manual'
        self.factory.prewrite_change = None
        repo = DeviceRepository(':memory:')
        self.addCleanup(repo.close)
        self.service = ScannerService(repository=repo, transport_factory=lambda *args: RpcTransport(self.factory, *args))
        self.service.set_default_credentials(Credentials('super', 'fixture-password'))
        self.record = self.service.poll('192.0.2.247')
        self.pause = patch.object(Operation, 'pause', lambda *_: None)
        self.pause.start()
        self.addCleanup(self.pause.stop)

    def execute(self, action, **kwargs):
        return execute_command(self.service, '192.0.2.247', action, **kwargs)

    def test_live_shape_identifies_protocol_and_firmware_without_write(self):
        self.assertEqual(self.record.identity.model, 'M61_VL50')
        self.assertEqual(self.record.identity.api_version, '3.0.5')
        self.assertEqual(self.record.identity.firmware_version, '20260908.12.Rel2')
        self.assertEqual(self.record.capabilities['mining_stop'], 'supported')
        self.assertEqual(self.record.capabilities['low'], 'unsupported')
        self.assertFalse(self.factory.writes)
        evidence = json.dumps(self.record.to_legacy())
        self.assertNotIn('testsalt', evidence)
        self.assertNotIn('fixture-password', evidence)

    def test_sleep_wake_and_token_on_same_connection(self):
        with patch('miner_scanner.commands.time.time', return_value=1700000000):
            self.assertEqual(self.execute('sleep').status, 'succeeded')
            self.assertEqual(self.execute('normal').status, 'succeeded')
        self.assertEqual(self.factory.ports, [4433, 4433])
        self.assertEqual(self.factory.writes[0], {'cmd': 'set.miner.service', 'param': 'stop',
                         'ts': 1700000000, 'token': 'SdyHB2ty', 'account': 'super'})
        self.assertEqual(self.factory.writes[1]['param'], 'start')
        self.assertNotIn('fixture-password', json.dumps(self.factory.writes))

    def test_led_readback_and_reboot_without_null_param(self):
        for action in ('led_on', 'led_off'):
            self.assertEqual(self.execute(action).status, 'succeeded')
        self.assertIsInstance(self.factory.writes[0]['param'], list)
        self.assertEqual(self.factory.writes[1]['param'], 'auto')
        result = self.execute('reboot')
        self.assertEqual(result.status, 'unconfirmed')
        self.assertTrue(result.accepted_by_api)
        self.assertNotIn('param', self.factory.writes[-1])

    def test_hardware_flash_state_confirms_led_and_keeps_switch_off_available(self):
        self.factory.led_on_state = 'flash'
        self.assertEqual(self.execute('led_on').status, 'succeeded')
        record = self.service.poll('192.0.2.247')
        self.assertEqual(record.capabilities['identify_off'], 'supported')
        self.assertEqual(self.execute('led_off').status, 'succeeded')
        self.assertEqual(self.factory.data['rpc_info']['msg']['system']['ledstatus'], 'auto')

    def test_official_default_profile_authorizes_without_manual_credentials(self):
        from miner_scanner.access import AccessProfiles, standard_profiles
        self.service.set_default_credentials(None)
        self.service.set_access_profiles(AccessProfiles(standard_profiles()))
        self.assertEqual(self.execute('led_on').status, 'succeeded')
        self.assertEqual(self.service.credentials_for('192.0.2.247'), Credentials('super', 'super'))
        self.assertEqual(len(self.factory.writes), 1)

    def test_permission_error_explains_access_without_retry(self):
        self.factory.code = -4
        result = self.execute('sleep')
        self.assertEqual(result.status, 'failed')
        self.assertIn('API Write', result.message)
        self.assertIn('-4', result.message)
        self.assertEqual(len(self.factory.writes), 1)

    def test_closed_write_switch_blocks_even_experimental(self):
        self.factory.data['rpc_info']['msg']['system']['apiswitch'] = '0'
        result = self.execute('sleep', allow_unverified=True)
        self.assertEqual(result.status, 'unsupported')
        self.assertIn('API Write', result.message)
        self.assertFalse(self.factory.writes)

    def test_invalid_account_never_sends_write(self):
        for account in ('root', 'admin'):
            self.service.set_default_credentials(Credentials(account, 'fixture-password'))
            result = self.execute('sleep')
            self.assertEqual(result.status, 'failed')
            self.assertIn('super', result.message)
        self.assertFalse(self.factory.writes)

    def test_restricted_user_account_is_preserved(self):
        self.service.set_default_credentials(Credentials('user2', 'fixture-password'))
        self.assertEqual(self.execute('sleep').status, 'succeeded')
        self.assertEqual(self.factory.writes[0]['account'], 'user2')

    def test_changed_prewrite_identity_or_switch_blocks(self):
        for change in (lambda r: r['msg']['miner'].update(type='M60'),
                       lambda r: r['msg']['system'].update(apiswitch='0'),
                       lambda r: r['msg']['system'].update(fwversion='other')):
            self.factory.prewrite_change = change
            self.assertEqual(self.execute('sleep').status, 'failed')
        self.assertFalse(self.factory.writes)

    def test_unknown_protocol_and_malformed_success_are_not_compatible(self):
        for api in ('2.0.0', '3.1.0', '4.0.0', None):
            response = info()
            response['msg']['system']['api'] = api
            self.assertFalse(resolve(response)['rules'])
        for code in (False, '0', -4):
            response = info()
            response['code'] = code
            self.assertFalse(resolve(response)['rules'])

    def test_ack_without_state_change_is_unconfirmed(self):
        self.factory.apply_mode = False
        result = self.execute('sleep')
        self.assertEqual(result.status, 'unconfirmed')
        self.assertTrue(result.accepted_by_api)
        self.assertEqual(len(self.factory.writes), 1)

    def test_timeout_and_reused_command_id_do_not_replay(self):
        self.factory.write_error = TimeoutError()
        result = self.execute('sleep', command_id='once')
        self.assertEqual(result.status, 'unconfirmed')
        self.assertEqual(self.execute('sleep', command_id='once'), result)
        self.assertEqual(len(self.factory.writes), 1)

    def test_wrong_response_command_is_rejected(self):
        self.factory.desc = 'set.miner.pools'
        self.assertEqual(self.execute('sleep').status, 'failed')
