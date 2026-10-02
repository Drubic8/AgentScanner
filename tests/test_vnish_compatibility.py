"""Locate-miner compatibility and execution against an offline REST device."""
from copy import deepcopy
import hashlib
import unittest
from unittest.mock import patch

from miner_scanner import vnish_compatibility as compat
from miner_scanner.access import AccessProfiles, standard_profiles
from miner_scanner.commands import execute_command
from miner_scanner.repository import DeviceRepository
from miner_scanner.runtime import Operation
from miner_scanner.service import ScannerService
from tests.fakes import FakeFactory, FakeTransport


class VnishTransport(FakeTransport):
    def http(self, path, method='GET', *, payload=None, headers=None, **kwargs):
        if method == 'GET':
            self.factory.calls.append((self.ip, path))
            return (200, self.factory.web[path]) if path in self.factory.web else (404, b'')
        self.factory.writes.append((path, method, deepcopy(payload), headers))
        if path == '/api/v1/locate-miner' and headers == {'Authorization': 'Bearer fixture-token'}:
            self.factory.data['/api/v1/status']['find_miner'] = payload['is_enabled']
            return 200, b'{}'
        raise AssertionError('Unexpected control write')

    def http_json(self, path, method='GET', **kwargs):
        if path == '/api/v1/unlock':
            self.factory.calls.append((self.ip, path))
            return {'token': 'fixture-token'}
        return super().http_json(path, method, **kwargs)


class VnishCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.body = b'/* synthetic reviewed locate-miner interface */'
        catalog = deepcopy(compat.interfaces())
        catalog['index'] = {hashlib.sha256(self.body).hexdigest(): next(iter(catalog['index'].values()))}
        self.addCleanup(patch.stopall)
        patch.object(compat, 'interfaces', return_value=catalog).start()
        patch.object(Operation, 'pause', lambda *_: None).start()
        self.factory = FakeFactory({
            'vnish_info': {'miner': 'Antminer S21+', 'model': 's21plus', 'fw_name': 'Vnish',
                           'fw_version': 'arbitrary build', 'hr_measure': 'GH/s'},
            'vnish_summary': {'miner': {'hr_realtime': 191000, 'hr_average': 192000,
                                      'miner_status': {'miner_state': 'mining'}}},
            '/api/v1/status': {'find_miner': False, 'miner_state': 'mining'},
        })
        self.factory.web = {'/index.html': b'<script src="/assets/index-audit.js"></script>',
                            '/assets/index-audit.js': self.body}
        self.repo = DeviceRepository(':memory:')
        self.addCleanup(self.repo.close)
        self.service = ScannerService(repository=self.repo, transport_factory=lambda *a: VnishTransport(self.factory, *a))
        self.service.set_access_profiles(AccessProfiles(standard_profiles()))
        self.record = self.service.poll('192.0.2.1')

    def test_locate_on_off_use_same_bearer_token_and_preserve_mining(self):
        self.assertEqual(self.record.identity.profile_id, 'bitmain.vnish')
        self.assertEqual(self.record.capabilities['identify_on'], 'supported')
        summary = deepcopy(self.factory.data['vnish_summary'])
        self.assertFalse(self.factory.writes)
        for action, enabled in [('led_on', True), ('led_off', False)]:
            result = execute_command(self.service, '192.0.2.1', action)
            self.assertEqual(result.status, 'succeeded')
            self.assertEqual(self.factory.writes[-1], ('/api/v1/locate-miner', 'POST',
                             {'is_enabled': enabled}, {'Authorization': 'Bearer fixture-token'}))
            self.assertIs(self.factory.data['/api/v1/status']['find_miner'], enabled)
        self.assertEqual(self.factory.data['vnish_summary'], summary)
        self.assertNotIn('mining_stop', self.record.display['ControlCompatibility']['compatible_commands'])

    def test_ui_is_rechecked_before_write_and_unknown_ui_is_blocked(self):
        self.factory.web['/assets/index-audit.js'] += b'changed'
        self.assertEqual(execute_command(self.service, '192.0.2.1', 'led_on', allow_unverified=True).status, 'unsupported')
        self.assertFalse(self.factory.writes)
        self.assertNotIn(('192.0.2.1', '/api/v1/unlock'), self.factory.calls)

    def test_boolean_status_is_required(self):
        for value in ('false', 0, None):
            with self.subTest(value=value):
                self.factory.data['/api/v1/status']['find_miner'] = value
                self.assertEqual(execute_command(self.service, '192.0.2.1', 'led_on').status, 'unsupported')
                self.assertFalse(self.factory.writes)

    def test_duplicate_command_does_not_repeat_write(self):
        result = execute_command(self.service, '192.0.2.1', 'led_on', command_id='locate-once')
        self.assertEqual(execute_command(self.service, '192.0.2.1', 'led_on', command_id='locate-once'), result)
        self.assertEqual(len(self.factory.writes), 1)

    def test_discovery_cache_avoids_reloading_bundle(self):
        before = self.factory.calls.count(('192.0.2.1', '/assets/index-audit.js'))
        self.service.poll('192.0.2.1')
        self.assertEqual(self.factory.calls.count(('192.0.2.1', '/assets/index-audit.js')), before)
        self.service.poll('192.0.2.1', force_identify=True)
        self.assertEqual(self.factory.calls.count(('192.0.2.1', '/assets/index-audit.js')), before + 1)

    def test_arbitrary_device_links_are_not_requested(self):
        for source in ('https://example.invalid/assets/index-x.js', '//example.invalid/assets/index-x.js',
                       '/api/v1/system/reboot', '/assets/index-x.js?reboot', '/assets/../index-x.js'):
            parser = compat.ScriptPaths()
            parser.feed(f'<script src="{source}"></script>')
            self.assertEqual(parser.paths, [])

    def test_non_vnish_identity_cannot_enable_contract(self):
        evidence = compat.probe(VnishTransport(self.factory, '192.0.2.1', Operation()))
        for info in ({'fw_name': 'Stock', 'miner': 'Antminer S21+'}, {'fw_name': 'Vnish'}, None):
            self.assertFalse(compat.resolve(info, evidence)['rules'])
