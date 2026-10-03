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


def api_spec(api='find-miner', *, both=False, core=False):
    """Minimal synthetic forms of the shipped 1.2.6 / 1.3.3 OpenAPI files."""
    spec = {'openapi': '3.1.0', 'servers': [{'url': '/api/v1'}], 'paths': {},
            'components': {'schemas': {}}}
    for name in (('find-miner', 'locate-miner') if both else (api,)):
        field = 'on' if name == 'find-miner' else 'is_enabled'
        schema_name = 'FindMinerStatus' if name == 'find-miner' else 'LocateMinerStatus'
        spec['components']['schemas'][schema_name] = {'type': 'object', 'required': [field],
                                                     'properties': {field: {'type': 'boolean'}}}
        response = {'$ref': '#/components/schemas/' + schema_name}
        if name == 'find-miner':
            response = {'oneOf': [{'type': 'null'}, response]}
        spec['paths']['/' + name] = {'post': {'responses': {'200': {
            'content': {'application/json': {'schema': response}}}}}}
        if both and name == 'find-miner':
            spec['paths']['/' + name]['post']['deprecated'] = True
    if core:
        for path in compat.CORE_PATHS.values():
            spec['paths'][path] = {'post': {'responses': {'200': {'description': 'success'}}}}
    return spec


class VnishTransport(FakeTransport):
    def http(self, path, method='GET', *, payload=None, headers=None, **kwargs):
        if method == 'GET':
            self.factory.calls.append((self.ip, path))
            return (200, self.factory.web[path]) if path in self.factory.web else (404, b'')
        self.factory.writes.append((path, method, deepcopy(payload), headers))
        if path == '/api/v1/locate-miner' and headers == {'Authorization': 'Bearer fixture-token'}:
            self.factory.data['/api/v1/status']['find_miner'] = payload['is_enabled']
            return 200, b'{}'
        if path == '/api/v1/find-miner' and headers == {'Authorization': 'Bearer fixture-token'}:
            self.factory.data['/api/v1/status']['find_miner'] = payload['on']
            return 200, b'{}'
        if path in {'/api/v1' + p for p in compat.CORE_PATHS.values()} and headers == {'Authorization': 'Bearer fixture-token'}:
            if payload is not None:
                raise AssertionError('Unexpected core command body')
            if path.endswith('/stop'):
                self.factory.data['vnish_summary']['miner']['miner_status']['miner_state'] = 'stopped'
            elif path.endswith('/start'):
                self.factory.data['vnish_summary']['miner']['miner_status']['miner_state'] = 'mining'
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
        self.legacy_body = b'/* synthetic reviewed find-miner interface */'
        self.legacy_body += b' ' * (1_420_958 - len(self.legacy_body))
        definitions = {definition['led_api']: definition for definition in catalog['index'].values()}
        catalog['index'] = {hashlib.sha256(self.body).hexdigest(): definitions['locate-miner'],
                           hashlib.sha256(self.legacy_body).hexdigest(): definitions['find-miner']}
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

    def test_legacy_find_miner_sets_boolean_and_does_not_toggle(self):
        self.factory.data['vnish_info'].update(miner='Antminer L9', fw_version='1.2.6')
        self.factory.web['/assets/index-audit.js'] = self.legacy_body
        record = self.service.poll('192.0.2.1', force_identify=True)
        self.assertEqual(record.capabilities['identify_on'], 'supported')
        for action, enabled in [('led_on', True), ('led_on', True), ('led_off', False), ('led_off', False)]:
            self.assertEqual(execute_command(self.service, '192.0.2.1', action).status, 'succeeded')
            self.assertEqual(self.factory.writes[-1], ('/api/v1/find-miner', 'POST',
                             {'on': enabled}, {'Authorization': 'Bearer fixture-token'}))
            self.assertIs(self.factory.data['/api/v1/status']['find_miner'], enabled)
        self.assertEqual(len(self.factory.writes), 2)
        self.assertEqual(self.factory.data['vnish_summary']['miner']['miner_status']['miner_state'], 'mining')

    def test_toggle_firmware_repeated_on_and_off_never_undo_target(self):
        self.factory.data['/docs/api-doc.json'] = api_spec()
        original = VnishTransport.http
        def toggle(transport, path, method='GET', **kwargs):
            if path == '/api/v1/find-miner' and method == 'POST':
                self.factory.writes.append((path, method, kwargs.get('payload')))
                self.factory.data['/api/v1/status']['find_miner'] = not self.factory.data['/api/v1/status']['find_miner']
                return 200, b'null'
            return original(transport, path, method, **kwargs)
        with patch.object(VnishTransport, 'http', toggle):
            for action, expected in [('led_on', True), ('led_on', True), ('led_off', False), ('led_off', False)]:
                result = execute_command(self.service, '192.0.2.1', action)
                self.assertEqual(result.status, 'succeeded')
                self.assertIs(self.factory.data['/api/v1/status']['find_miner'], expected)
        self.assertEqual(len(self.factory.writes), 2)

    def test_unknown_authenticated_led_state_never_sends_toggle(self):
        original = VnishTransport.http_json
        def unavailable(transport, path, method='GET', **kwargs):
            if path == '/api/v1/status' and kwargs.get('headers'):
                return {'find_miner': 'false'}
            return original(transport, path, method, **kwargs)
        with patch.object(VnishTransport, 'http_json', unavailable):
            self.assertEqual(execute_command(self.service, '192.0.2.1', 'led_on').status, 'failed')
        self.assertFalse(self.factory.writes)

    def test_probe_requests_asset_budget_without_changing_json_budget(self):
        transport = VnishTransport(self.factory, '192.0.2.1', Operation())
        original = transport.http
        with patch.object(transport, 'http', wraps=original) as request:
            compat.probe(transport)
        asset_call = next(call for call in request.call_args_list if call.args[0] == '/assets/index-audit.js')
        self.assertEqual(asset_call.kwargs,
                         {'response_limit': transport.operation.options.max_asset_bytes})
        self.assertEqual(transport.operation.options.max_response_bytes, 1_048_576)

    def test_boolean_status_is_required(self):
        for value in ('false', 0, None):
            with self.subTest(value=value):
                self.factory.data['/api/v1/status']['find_miner'] = value
                self.assertEqual(execute_command(self.service, '192.0.2.1', 'led_on').status, 'unsupported')
                self.assertFalse(self.factory.writes)

    def test_openapi_works_across_versions_without_reading_ui_scripts(self):
        for api, version, field in [('find-miner', '1.2.6', 'on'), ('locate-miner', 'different build', 'is_enabled')]:
            self.factory.data['/docs/api-doc.json'] = api_spec(api)
            self.factory.data['vnish_info']['fw_version'] = version
            self.factory.web['/assets/index-audit.js'] = b'/* unknown frontend */'
            self.service.poll('192.0.2.1', force_identify=True)
            self.factory.calls.clear()
            for action, enabled in [('led_on', True), ('led_off', False)]:
                self.assertEqual(execute_command(self.service, '192.0.2.1', action).status, 'succeeded')
                self.assertEqual(self.factory.writes[-1], ('/api/v1/' + api, 'POST',
                    {field: enabled}, {'Authorization': 'Bearer fixture-token'}))
            self.assertNotIn(('192.0.2.1', '/index.html'), self.factory.calls)
            self.assertNotIn(('192.0.2.1', '/assets/index-audit.js'), self.factory.calls)

    def test_modern_openapi_prefers_locate_over_deprecated_find(self):
        self.factory.data['/docs/api-doc.json'] = api_spec(both=True)
        self.assertEqual(execute_command(self.service, '192.0.2.1', 'led_on').status, 'succeeded')
        self.assertEqual(self.factory.writes[-1][0:3], ('/api/v1/locate-miner', 'POST', {'is_enabled': True}))

    def test_changed_api_schema_overrides_previously_recognized_ui(self):
        spec = api_spec()
        self.factory.data['/docs/api-doc.json'] = spec
        spec['components']['schemas']['FindMinerStatus']['properties']['on']['type'] = 'string'
        self.assertEqual(execute_command(self.service, '192.0.2.1', 'led_on', allow_unverified=True).status, 'unsupported')
        self.assertFalse(self.factory.writes)

    def test_required_request_body_must_match_known_setter(self):
        spec = api_spec()
        self.factory.data['/docs/api-doc.json'] = spec
        operation = spec['paths']['/find-miner']['post']
        operation['requestBody'] = {'content': {'application/json': {'schema': {
            'type': 'object', 'required': ['on', 'new-required-field'], 'properties': {'on': {'type': 'boolean'}}}}}}
        self.assertEqual(execute_command(self.service, '192.0.2.1', 'led_on').status, 'unsupported')
        self.assertFalse(self.factory.writes)

    def test_only_declared_no_body_core_commands_are_granted(self):
        spec = api_spec(core=True)
        self.factory.data['/docs/api-doc.json'] = spec
        for action, path in [('sleep', '/mining/stop'), ('wakeup', '/mining/start'), ('reboot', '/system/reboot')]:
            result = execute_command(self.service, '192.0.2.1', action)
            self.assertEqual(result.status, 'unconfirmed' if action == 'reboot' else 'succeeded')
            self.assertEqual(self.factory.writes[-1], ('/api/v1' + path, 'POST', None,
                                                      {'Authorization': 'Bearer fixture-token'}))
        spec['paths']['/mining/stop']['post']['requestBody'] = {'required': True}
        self.assertEqual(execute_command(self.service, '192.0.2.1', 'sleep').status, 'unsupported')
        self.assertEqual(len(self.factory.writes), 3)

    def test_malformed_or_external_schema_is_not_followed_or_executed(self):
        variants = []
        spec = api_spec()
        spec['servers'] = [{'url': 'https://example.invalid/api/v1'}]
        variants.append(spec)
        spec = api_spec()
        spec['components']['schemas']['FindMinerStatus'] = {'$ref': 'https://example.invalid/schema.json'}
        variants.append(spec)
        spec = api_spec()
        spec['components']['schemas']['FindMinerStatus'] = {'$ref': '#/components/schemas/FindMinerStatus'}
        variants.append(spec)
        spec = api_spec()
        spec['components'] = None
        variants.append(spec)
        for spec in variants:
            self.factory.data['/docs/api-doc.json'] = spec
            self.assertEqual(execute_command(self.service, '192.0.2.1', 'led_on').status, 'unsupported')
        self.assertFalse(self.factory.writes)
        self.assertFalse(any('example.invalid' in path for _, path in self.factory.calls))

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
