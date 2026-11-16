"""RPC3 pool planning, encryption, runtime readback and journaling, offline."""
import base64
from contextlib import nullcontext
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import requests
try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except ImportError:
    Cipher = None

from miner_scanner.drivers import make_record
from miner_scanner.models import Credentials
from miner_scanner.pool_configuration import PoolEdit, WorkerNaming, prepare_pool_batch, execute_pool_batch
from miner_scanner.pool_evidence import evidence_report
from miner_scanner.profiles import ProfileRegistry
from tests.test_pool_integration import OfflineDevice, OfflineService


FIXTURE = Path(__file__).parent / 'fixtures' / 'whatsminer_m50_rpc3_pools_read.json'
API_PASSWORD = 'synthetic-api-secret'
POOL_PASSWORDS = ('  pool:пароль,1  ', '***', '')


class RpcDevice:
    def __init__(self):
        fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
        self.data = {('get.device.info', None): fixture['rpc_info'],
                     ('get.miner.status', 'pools'): fixture['rpc_pools'],
                     ('get.miner.setting', None): {
                         'code': 0, 'desc': 'get.miner.setting',
                         'msg': {'btrom-worker-suffix': ''}}}
        self.writes = []
        self.read_count = 0
        self.last_salt = None
        self.write_error = None
        self.apply = True

    def packet(self, packet):
        command, parameter = packet['cmd'], packet.get('param')
        if command != 'set.miner.pools':
            result = deepcopy(self.data[(command, parameter)])
            self.read_count += 1
            if command == 'get.device.info':
                self.last_salt = 'synthetic-changing-salt-' + str(self.read_count)
                result['msg']['salt'] = self.last_salt
            elif command == 'get.miner.status':
                for pool in result['msg']['pools']:
                    pool['last-share-time'] += self.read_count
            return result
        # Decode independently of the production encoder. These assertions make
        # the integration fail if API credentials replace a pool password,
        # JSON is encoded twice, or padding/account/token fields are incorrect.
        if set(packet) != {'cmd', 'account', 'ts', 'token', 'param'}:
            raise AssertionError('Unexpected authenticated RPC fields')
        if packet['account'] != 'super' or type(packet['ts']) is not int:
            raise AssertionError('Wrong API account or timestamp')
        digest = hashlib.sha256((command + API_PASSWORD + self.last_salt
                                 + str(packet['ts'])).encode('utf-8')).digest()
        if packet['token'] != base64.b64encode(digest).decode('ascii')[:8]:
            raise AssertionError('Wrong authenticated RPC token')
        decryptor = Cipher(algorithms.AES(digest), modes.ECB()).decryptor()
        raw = decryptor.update(base64.b64decode(parameter, validate=True)) + decryptor.finalize()
        padding = raw[-1]
        if not 1 <= padding <= 16 or raw[-padding:] != bytes([padding]) * padding:
            raise AssertionError('Expected the official Python reference PKCS7 padding')
        payload = json.loads(raw[:-padding])
        if not isinstance(payload, list) or len(payload) != 3:
            raise AssertionError('Expected three pool objects, serialized once')
        if any(set(item) != {'pool', 'worker', 'passwd'} for item in payload):
            raise AssertionError('Unexpected pool field names')
        self.writes.append(payload)
        if self.apply:
            for current, new in zip(self.data[('get.miner.status', 'pools')]['msg']['pools'], payload):
                current['url'], current['account'] = new['pool'], new['worker']
        if self.write_error:
            raise self.write_error
        return {'code': 0, 'desc': command, 'msg': {}}


class RpcTransport:
    def __init__(self, device):
        self.device = device

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def rpc(self, command, parameter=None, *, port=4433):
        if port != 4433:
            raise AssertionError('Unexpected RPC port')
        packet = {'cmd': command}
        if parameter is not None:
            packet['param'] = parameter
        return self.device.packet(packet)

    def connection(self, port):
        if port != 4433:
            raise AssertionError('Unexpected control port')
        return nullcontext(object())

    def rpc_packet(self, sock, payload):
        return self.device.packet(payload)


@unittest.skipUnless(Cipher is not None, 'Optional cryptography package is unavailable')
class WhatsminerPoolIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.ip = '192.0.2.50'
        self.device = RpcDevice()
        self.service = OfflineService({self.ip: OfflineDevice()})
        self.addCleanup(self.service.repository.close)
        registry = ProfileRegistry()
        fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
        record = make_record(registry.by_id['microbt.rpc3'], self.ip,
                             {key: fixture[key] for key in ('rpc_info', 'rpc_pools', 'rpc_summary')})
        self.service.records[self.ip] = record
        self.service.registry = registry
        self.service.credentials_for = lambda ip: Credentials('super', API_PASSWORD)
        self.service.transport_factory = lambda *args: RpcTransport(self.device)
        pause = patch('miner_scanner.pool_configuration.Operation.pause', return_value=None)
        pause.start()
        self.addCleanup(pause.stop)

    def prepare(self, edits=None):
        return list(prepare_pool_batch(self.service, [self.service.records[self.ip].to_legacy()],
                    edits if edits is not None else [
                        PoolEdit(i, worker=WorkerNaming('TEST', 'ip'), password=password)
                        for i, password in enumerate(POOL_PASSWORDS)]))

    def execute(self, plans):
        return dict(execute_pool_batch(self.service, plans))[self.ip]

    def test_all_three_exact_passwords_reach_one_encrypted_request(self):
        plans = self.prepare()
        self.assertEqual(plans[0].status, 'ready', plans[0].message)
        self.assertFalse(self.device.writes)
        report = evidence_report(plans)
        self.assertEqual(report['devices'][0]['backend'], 'whatsminer.rpc3.pools')
        self.assertEqual(report['devices'][0]['reading_kind'], 'runtime')
        result = self.execute(plans)
        self.assertTrue(result.accepted_by_api)
        self.assertEqual(result.status, 'unconfirmed', result.message)
        self.assertEqual(len(self.device.writes), 1)
        payload = self.device.writes[0]
        self.assertEqual([item['passwd'] for item in payload], list(POOL_PASSWORDS))
        self.assertEqual([item['worker'] for item in payload], ['TEST.192_0_2_50'] * 3)
        self.assertEqual(self.service.records[self.ip].display['Worker'], 'TEST.192_0_2_50')
        self.assertEqual(self.execute(plans), result)
        self.assertEqual(len(self.device.writes), 1)
        public = repr(plans) + repr(result) + json.dumps(report)
        for secret in (API_PASSWORD, POOL_PASSWORDS[0], 'synthetic-changing-salt'):
            self.assertNotIn(secret, public)

    def test_password_for_untouched_slot_must_be_explicit(self):
        plans = self.prepare([PoolEdit(0, password='new-secret')])
        self.assertEqual(plans[0].status, 'unsupported')
        self.assertIsNone(plans[0]._prepared)
        self.assertFalse(self.device.writes)
        self.assertIsNone(self.service.repository.command_result(plans[0].command_id))

    def test_timeout_after_apply_never_repeats_request_or_claims_password_readback(self):
        plans = self.prepare()
        self.device.write_error = requests.ReadTimeout('synthetic-secret-error')
        result = self.execute(plans)
        self.assertFalse(result.accepted_by_api)
        self.assertEqual(result.status, 'unconfirmed')
        self.assertEqual(self.execute(plans), result)
        self.assertEqual(len(self.device.writes), 1)

    def test_changed_pool_after_preview_is_rejected_without_write(self):
        plans = self.prepare()
        self.device.data[('get.miner.status', 'pools')]['msg']['pools'][0]['account'] = 'external.change'
        self.assertEqual(self.execute(plans).status, 'skipped')
        self.assertFalse(self.device.writes)


if __name__ == '__main__':
    unittest.main()
