"""Contract discovery and command execution against a synthetic HTTP device."""
from copy import deepcopy
import hashlib
import time
import unittest
from unittest.mock import patch

from miner_scanner import stock_compatibility as compat
from miner_scanner.commands import execute_command, declarative_command
from miner_scanner.models import Credentials
from miner_scanner.repository import DeviceRepository
from miner_scanner.runtime import DeadlineExceeded, Operation, ScanOptions
from miner_scanner.service import ScannerService
from tests.fakes import FakeFactory, FakeTransport
from tests.test_stock_mode_timeout import device


class WebTransport(FakeTransport):
    def http(self, path, method='GET', *, payload=None, **kwargs):
        if method == 'GET' and path in self.factory.web:
            self.factory.calls.append((self.ip, path))
            return 200, self.factory.web[path]
        if method == 'GET' and path == '/reboot.html':
            self.factory.calls.append((self.ip, path))
            return 404, b''
        response = super().http(path, method, payload=payload, **kwargs)
        if method == 'POST' and path.endswith('set_miner_conf.cgi'):
            target = payload.get('miner-mode', payload.get('bitmain-work-mode'))
            previous = self.factory.data['config']['bitmain-work-mode']
            self.factory.data['config']['bitmain-work-mode'] = str(target) if type(previous) is str else target
            return 200, b'{"code":"M000","stats":"success"}'
        if method == 'POST' and path.endswith('blink.cgi'):
            self.factory.data['/cgi-bin/get_blink_status.cgi'] = {'blink': payload['blink']}
            return 200, b'{"code":"B000"}' if payload['blink'] else b'{"code":"B100"}'
        return response


class StockCompatibilityTests(unittest.TestCase):
    def setUp(self):
        # Synthetic byte bodies select the same production contracts. No real
        # miner code is executed or required to run this suite offline.
        catalog = deepcopy(compat.interfaces())
        self.bodies = {}
        for kind in ('miner', 'index'):
            definitions = {}
            for definition in catalog[kind].values():
                name = definition['source_asset']
                body = ('/* synthetic ' + name + ' */').encode()
                self.bodies[name] = body
                definitions[hashlib.sha256(body).hexdigest()] = definition
            catalog[kind] = definitions
        legacy = {}
        for number, definition in enumerate(catalog['legacy_reboot'].values()):
            body = ('<!-- synthetic legacy reboot ' + str(number) + ' -->').encode()
            self.bodies['legacy' + str(number)] = body
            legacy[hashlib.sha256(body).hexdigest()] = definition
        catalog['legacy_reboot'] = legacy
        self.addCleanup(patch.stopall)
        patch.object(compat, 'interfaces', return_value=catalog).start()
        patch.object(Operation, 'pause', lambda *_: None).start()

    def service(self, model='Antminer T21', asset='miner.b91570.js', version='arbitrary build'):
        data = device(model, version)
        if asset == 'miner.0ef2dc.js':
            data['config'].pop('bitmain-hashrate-percent')
            data['config']['bitmain-freq-level'] = '100'
        elif asset == 'miner.dc8dd8.js':
            data['config'].pop('bitmain-hashrate-percent')
            data['config'].update({'algo': 'dash_1766', 'bitmain-freq': '0',
                                  'bitmain-voltage': '0', 'bitmain-freq-level': ''})
        elif asset == 'miner.c8af43.js':
            data['config'].pop('bitmain-hashrate-percent')
            data['config'].update({'bitmain-user-ip-cat': False, 'bitmain-freq': 650,
                                  'bitmain-voltage': 14.0, 'bitmain-freq-level': 100,
                                  'bitmain-work-mode': 1})
        factory = FakeFactory(data)
        factory.apply_mode = False
        factory.data['/cgi-bin/get_blink_status.cgi'] = {'blink': False}
        factory.web = {'/miner.html': f'<script src="/js/{asset}"></script>'.encode(),
                       '/js/' + asset: self.bodies[asset],
                       '/index.html': b'<script src="js/index.4be6b1.js"></script>',
                       '/js/index.4be6b1.js': self.bodies['index.4be6b1.js']}
        repo = DeviceRepository(':memory:')
        self.addCleanup(repo.close)
        service = ScannerService(repository=repo, transport_factory=lambda *args: WebTransport(factory, *args))
        service.set_default_credentials(Credentials('root', 'root'))
        record = service.poll('192.0.2.1')
        return service, factory, record

    def test_unknown_firmware_uses_compatible_api_without_experimental_flag(self):
        for model, asset, action, target, field in (
            ('Antminer T21', 'miner.b91570.js', 'hem', 2, 'miner-mode'),
            ('Antminer S21', 'miner.2a6d03.js', 'low', 3, 'miner-mode'),
            ('Antminer L9', 'miner.0ef2dc.js', 'normal', 0, 'miner-mode'),
            ('Antminer S21+', 'miner.c8af43.js', 'sleep', 1, 'bitmain-work-mode'),
        ):
            with self.subTest(model=model):
                service, factory, record = self.service(model, asset)
                self.assertEqual(record.identity.profile_id, 'bitmain.stock')
                self.assertFalse(record.display['ControlCompatibility']['hardware_verified_commands'])
                baseline = deepcopy(factory.data['config'])
                result = execute_command(service, '192.0.2.1', action)
                self.assertEqual(result.status, 'succeeded')
                payload = factory.writes[-1][2]
                self.assertEqual(payload[field], target)
                self.assertEqual(payload['pools'], baseline['pools'])
                self.assertEqual(payload['bitmain-fan-pwm'], baseline['bitmain-fan-pwm'])
                self.assertNotIn('bitmain-work-mode' if field == 'miner-mode' else 'miner-mode', payload)

    def test_same_model_has_different_modes_and_write_keys(self):
        service, factory, record = self.service('Antminer S21+', 'miner.c8af43.js')
        self.assertEqual(record.capabilities['low'], 'unsupported')
        self.assertEqual(execute_command(service, '192.0.2.1', 'low', allow_unverified=True).status, 'unsupported')
        self.assertFalse(factory.writes)
        _, _, other = self.service('Antminer S21+', 'miner.b91570.js')
        self.assertEqual(other.capabilities['low'], 'supported')
        self.assertEqual(other.capabilities['hem'], 'unsupported')

    def test_d9_preserves_tuning_and_empty_frequency_on_sleep_and_normal(self):
        service, factory, record = self.service('Antminer D9', 'miner.dc8dd8.js')
        baseline = deepcopy(factory.data['config'])
        self.assertEqual(record.capabilities['low'], 'unsupported')
        self.assertEqual(record.capabilities['hem'], 'unsupported')
        for action, target in [('sleep', 1), ('normal', 0)]:
            self.assertEqual(execute_command(service, '192.0.2.1', action).status, 'succeeded')
            payload = factory.writes[-1][2]
            self.assertEqual(payload, {
                'pools': baseline['pools'], 'bitmain-fan-ctrl': baseline['bitmain-fan-ctrl'],
                'bitmain-fan-pwm': baseline['bitmain-fan-pwm'], 'bitmain-freq': '0',
                'bitmain-voltage': '0', 'freq-level': '', 'miner-mode': target,
            })

    def test_d9_new_index_supports_led_and_reboot(self):
        service, factory, _ = self.service('Antminer D9', 'miner.dc8dd8.js')
        factory.web['/index.html'] = b'<script src="/js/index.0e8706.js"></script>'
        factory.web['/js/index.0e8706.js'] = self.bodies['index.0e8706.js']
        for action in ('led_on', 'led_off', 'reboot'):
            result = execute_command(service, '192.0.2.1', action)
            self.assertEqual(result.status, 'unconfirmed' if action == 'reboot' else 'succeeded')
        self.assertEqual(factory.writes[-1], ('/cgi-bin/reboot.cgi', 'GET', None))

    def test_d9_missing_tuning_field_blocks_config_write(self):
        service, factory, _ = self.service('Antminer D9', 'miner.dc8dd8.js')
        del factory.data['config']['bitmain-voltage']
        self.assertEqual(execute_command(service, '192.0.2.1', 'sleep').status, 'unsupported')
        self.assertFalse(factory.writes)

    def test_led_on_off_and_reboot_use_contract(self):
        service, factory, record = self.service()
        for action in ('led_on', 'led_off', 'reboot'):
            result = execute_command(service, '192.0.2.1', action)
            self.assertEqual(result.status, 'unconfirmed' if action == 'reboot' else 'succeeded')
        self.assertEqual(factory.data['/cgi-bin/get_blink_status.cgi'], {'blink': False})
        self.assertEqual(factory.writes[-1], ('/cgi-bin/reboot.cgi', 'GET', None))

    def test_l9_december_2024_and_fr119_led_interfaces(self):
        for asset in ('index.084178.js', 'index.acb1b2.js'):
            with self.subTest(asset=asset):
                service, factory, _ = self.service('Antminer L9', 'miner.0ef2dc.js')
                factory.web['/index.html'] = f'<script src="/js/{asset}"></script>'.encode()
                factory.web['/js/' + asset] = self.bodies[asset]
                for action, enabled in [('led_on', True), ('led_off', False)]:
                    result = execute_command(service, '192.0.2.1', action)
                    self.assertEqual(result.status, 'succeeded')
                    self.assertEqual(factory.writes[-1], ('/cgi-bin/blink.cgi', 'POST', {'blink': enabled}))
                    self.assertIs(factory.data['/cgi-bin/get_blink_status.cgi']['blink'], enabled)
                factory.web['/js/' + asset] += b'unknown change'
                self.assertEqual(execute_command(service, '192.0.2.1', 'led_on').status, 'succeeded')
                self.assertEqual(len(factory.writes), 3)

    def test_ks5_shared_header_does_not_require_model_in_power_contract(self):
        service, factory, record = self.service('Antminer KS5')
        factory.web['/index.html'] = b'<script src="/js/index.084178.js"></script>'
        factory.web['/js/index.084178.js'] = self.bodies['index.084178.js']
        record = service.poll('192.0.2.1', force_identify=True)
        self.assertEqual(record.identity.make, 'Bitmain')
        self.assertEqual(record.capabilities['identify_on'], 'supported')
        for action in ('led_on', 'led_off'):
            self.assertEqual(execute_command(service, '192.0.2.1', action).status, 'succeeded')
        self.assertEqual(factory.writes, [('/cgi-bin/blink.cgi', 'POST', {'blink': True}),
                                         ('/cgi-bin/blink.cgi', 'POST', {'blink': False})])
        for action in ('sleep', 'normal', 'low', 'hem'):
            self.assertEqual(execute_command(service, '192.0.2.1', action).status, 'unsupported')
        self.assertEqual(len(factory.writes), 2)

    def test_shared_header_remains_scoped_to_bitmain_stock(self):
        _, _, record = self.service('Antminer KS5')
        for make, firmware in (('Elphapex', 'Stock'), ('Bitmain', 'VNish'), ('Unknown', 'Stock')):
            record.identity.make, record.identity.firmware = make, firmware
            self.assertFalse(compat.eligible(record))
            self.assertFalse(compat.resolve(record, {}, {'index': next(iter(compat.interfaces()['index'])),
                                                       'blink_boolean': True})['rules'])

    def test_ks5_led_uses_api_with_unknown_changed_or_missing_frontend(self):
        for web in ({}, {'/index.html': b'<script src="/js/index.future.js"></script>',
                         '/js/index.future.js': b'/* different firmware UI */'}):
            service, factory, _ = self.service('Antminer KS5', version='another stock build')
            factory.web = web
            baseline = deepcopy(factory.data['config'])
            for action, enabled in [('led_on', True), ('led_off', False)]:
                self.assertEqual(execute_command(service, '192.0.2.1', action).status, 'succeeded')
                self.assertEqual(factory.writes[-1], ('/cgi-bin/blink.cgi', 'POST', {'blink': enabled}))
            self.assertEqual(factory.data['config'], baseline)
            self.assertEqual(len(factory.writes), 2)

    def test_pitbit_uses_cgi_led_without_inheriting_stock_power_modes(self):
        service, factory, _ = self.service('Antminer S19')
        factory.data['version']['VERSION'][0]['fw_name'] = 'PitBit'
        factory.web = {}
        record = service.poll('192.0.2.1', force_identify=True)
        self.assertEqual(record.identity.profile_id, 'bitmain.pitbit')
        self.assertEqual(record.capabilities['identify_on'], 'supported')
        for action in ('led_on', 'led_off'):
            self.assertEqual(execute_command(service, '192.0.2.1', action).status, 'succeeded')
        self.assertNotIn('mining_stop', record.display['ControlCompatibility']['compatible_commands'])

    def test_z_series_modern_reboot_is_not_tied_to_build_date(self):
        service, factory, _ = self.service('Antminer Z15 Pro')
        factory.web['/index.html'] = b'<script src="/js/index.16ab47.js"></script>'
        factory.web['/js/index.16ab47.js'] = self.bodies['index.16ab47.js']
        result = execute_command(service, '192.0.2.1', 'reboot')
        self.assertEqual(result.status, 'unconfirmed')
        self.assertTrue(result.accepted_by_api)
        self.assertEqual(factory.writes, [('/cgi-bin/reboot.cgi', 'GET', None)])

    def test_legacy_z_series_only_enables_audited_reboot(self):
        for model in ('Antminer Z11', 'Antminer Z15', 'Antminer Z15 Pro'):
            for variant in ('legacy0', 'legacy1'):
                with self.subTest(model=model, variant=variant):
                    service, factory, _ = self.service(model)
                    factory.web = {'/index.html': b'<a href="/reboot.html">Reboot</a>',
                                   '/reboot.html': self.bodies[variant]}
                    del factory.data['/cgi-bin/get_blink_status.cgi']
                    record = service.poll('192.0.2.1', force_identify=True)
                    self.assertEqual(record.capabilities['reboot'], 'supported')
                    self.assertEqual(record.display['ControlCompatibility']['compatible_commands'], ['reboot'])
                    self.assertFalse(factory.writes)
                    self.assertEqual(execute_command(service, '192.0.2.1', 'reboot').status, 'unconfirmed')
                    self.assertEqual(factory.writes, [('/cgi-bin/reboot.cgi', 'GET', None)])

    def test_changed_or_missing_legacy_page_blocks_reboot(self):
        for body in (self.bodies['legacy0'] + b'changed', b'<a href="/cgi-bin/reboot.cgi">Reboot</a>', None):
            service, factory, _ = self.service('Antminer Z11')
            del factory.data['/cgi-bin/get_blink_status.cgi']
            factory.web = {'/index.html': b'old web interface'}
            if body is not None:
                factory.web['/reboot.html'] = body
            self.assertEqual(execute_command(service, '192.0.2.1', 'reboot').status, 'unsupported')
            self.assertFalse(factory.writes)

    def test_legacy_page_is_rechecked_before_reboot_and_never_replayed(self):
        service, factory, _ = self.service('Antminer Z15')
        del factory.data['/cgi-bin/get_blink_status.cgi']
        factory.web = {'/index.html': b'old web interface', '/reboot.html': self.bodies['legacy0']}
        service.poll('192.0.2.1', force_identify=True)
        factory.web['/reboot.html'] += b'changed'
        self.assertEqual(execute_command(service, '192.0.2.1', 'reboot').status, 'unsupported')
        self.assertFalse(factory.writes)
        factory.web['/reboot.html'] = self.bodies['legacy0']
        result = execute_command(service, '192.0.2.1', 'reboot', command_id='legacy-reboot')
        self.assertEqual(execute_command(service, '192.0.2.1', 'reboot', command_id='legacy-reboot'), result)
        self.assertEqual(factory.writes, [('/cgi-bin/reboot.cgi', 'GET', None)])

    def test_changed_interface_is_rechecked_before_any_write(self):
        service, factory, record = self.service()
        self.assertEqual(record.capabilities['hem'], 'supported')
        factory.web['/js/miner.b91570.js'] += b'/* changed */'
        result = execute_command(service, '192.0.2.1', 'hem', allow_unverified=True)
        self.assertEqual(result.status, 'unsupported')
        self.assertFalse(factory.writes)

    def test_unknown_config_field_blocks_modes(self):
        service, factory, record = self.service()
        factory.data['config']['new-required-setting'] = 123
        self.assertEqual(execute_command(service, '192.0.2.1', 'sleep').status, 'unsupported')
        self.assertFalse(factory.writes)

    def test_config_schema_is_checked_again_immediately_before_write(self):
        service, factory, record = self.service()
        def changed(transport, rule):
            factory.data['config']['additional-required-field'] = 1
            return declarative_command(transport, rule)
        with patch('miner_scanner.commands.declarative_command', changed):
            self.assertEqual(execute_command(service, '192.0.2.1', 'sleep').status, 'failed')
        self.assertFalse(factory.writes)

    def test_unknown_ui_keeps_basic_modes_when_config_contracts_agree(self):
        service, factory, record = self.service(version='Thu Jan 16 11:00:25 CST 2025')
        factory.web['/miner.html'] = b'<script src="/js/new-ui.js"></script>'
        self.assertEqual(execute_command(service, '192.0.2.1', 'sleep').status, 'succeeded')
        self.assertEqual(factory.writes[-1][2]['miner-mode'], 1)

    def test_unknown_stock_ui_selects_basic_modes_from_config_without_optional_modes(self):
        for model, asset in [('Antminer T21', 'miner.b91570.js'), ('Antminer L9', 'miner.0ef2dc.js'),
                             ('Antminer S21+', 'miner.c8af43.js'), ('Antminer D9', 'miner.dc8dd8.js')]:
            service, factory, _ = self.service(model, asset)
            factory.web = {}
            baseline = deepcopy(factory.data['config'])
            record = service.poll('192.0.2.1', force_identify=True)
            self.assertEqual(record.capabilities['mining_stop'], 'supported')
            self.assertEqual(record.capabilities['mining_start'], 'supported')
            self.assertEqual(record.display['ControlCompatibility']['interfaces']['mode_basis'], 'config_schema')
            for action in ('sleep', 'wakeup'):
                self.assertEqual(execute_command(service, '192.0.2.1', action).status, 'succeeded')
                self.assertEqual(factory.writes[-1][2]['pools'], baseline['pools'])
            for action in ('low', 'hem'):
                self.assertEqual(execute_command(service, '192.0.2.1', action, allow_unverified=True).status, 'unsupported')
            self.assertEqual(len(factory.writes), 2)

    def test_conflicting_config_to_write_mappings_do_not_authorize_power_write(self):
        service, factory, _ = self.service()
        catalog = deepcopy(compat.interfaces())
        conflicting = deepcopy(next(definition for definition in catalog['miner'].values()
                                    if 'T21' in definition['modes']))
        conflicting['write_mode'] = 'different-mode-key'
        catalog['miner']['unseen-conflicting-contract'] = conflicting
        factory.web = {}
        with patch.object(compat, 'interfaces', return_value=catalog):
            self.assertEqual(execute_command(service, '192.0.2.1', 'sleep').status, 'unsupported')
        self.assertFalse(factory.writes)

    def test_led_requires_boolean_readback_contract(self):
        service, factory, record = self.service()
        factory.data['/cgi-bin/get_blink_status.cgi'] = {'blink': 'false'}
        self.assertEqual(execute_command(service, '192.0.2.1', 'led_on').status, 'unsupported')
        self.assertFalse(factory.writes)

    def test_masked_password_blocks_write(self):
        service, factory, record = self.service()
        factory.data['config']['pools'][0]['pass'] = '***'
        self.assertEqual(execute_command(service, '192.0.2.1', 'sleep').status, 'failed')
        self.assertFalse(factory.writes)

    def test_empty_pool_slots_and_absent_password_preserved(self):
        service, factory, record = self.service()
        pools = [{'url': 'stratum+tcp://example.invalid:3333', 'user': 'fixture'}, {}, {}]
        factory.data['config']['pools'] = deepcopy(pools)
        self.assertEqual(execute_command(service, '192.0.2.1', 'sleep').status, 'succeeded')
        self.assertEqual(factory.writes[-1][2]['pools'], pools)

    def test_interface_cache_avoids_extra_reads_and_expires(self):
        service, factory, record = self.service()
        before = factory.calls.count(('192.0.2.1', '/miner.html'))
        service.poll('192.0.2.1')
        self.assertEqual(factory.calls.count(('192.0.2.1', '/miner.html')), before)
        service.poll('192.0.2.1', force_identify=True)
        self.assertEqual(factory.calls.count(('192.0.2.1', '/miner.html')), before + 1)
        fingerprint, _, evidence = service._interface_cache['192.0.2.1']
        # CI may have a monotonic clock younger than the cache TTL. Expire
        # relative to the current clock instead of assuming a long host uptime.
        expired = time.monotonic() - ScanOptions().metadata_ttl - 1
        service._interface_cache['192.0.2.1'] = (fingerprint, expired, evidence)
        service.poll('192.0.2.1')
        self.assertEqual(factory.calls.count(('192.0.2.1', '/miner.html')), before + 2)

    def test_untrusted_asset_links_are_never_requested(self):
        for source in ('https://example.invalid/js/miner.x.js', '//example.invalid/js/miner.x.js',
                       '/cgi-bin/reboot.cgi', '/js/../reboot.cgi', '/js/miner.x.js?cmd=reboot'):
            parser = compat.ScriptPaths('miner')
            parser.feed(f'<script src="{source}"></script>')
            self.assertEqual(parser.paths, [])

    def test_probe_deadline_retains_telemetry(self):
        service, factory, record = self.service()
        original = WebTransport.http
        def timeout(transport, path, *args, **kwargs):
            if path == '/miner.html':
                raise DeadlineExceeded()
            return original(transport, path, *args, **kwargs)
        with patch.object(WebTransport, 'http', timeout):
            current = service.poll('192.0.2.1', force_identify=True)
        self.assertFalse(current.telemetry.stale)
        self.assertEqual(current.display['Status'], record.display['Status'])
        self.assertEqual(current.display['ControlCompatibility']['compatible_commands'],
                         ['identify_off', 'identify_on', 'mining_start', 'mining_stop', 'reboot'])

    def test_idempotency_does_not_replay_write(self):
        service, factory, record = self.service()
        result = execute_command(service, '192.0.2.1', 'sleep', command_id='same')
        repeated = execute_command(service, '192.0.2.1', 'sleep', command_id='same')
        self.assertEqual(result, repeated)
        self.assertEqual(len(factory.writes), 1)
