"""KS5 read schemas match across builds whose web pages expose different modes."""
from copy import deepcopy
import hashlib
import unittest
from unittest.mock import patch

from miner_scanner import stock_compatibility as compat
from miner_scanner.commands import execute_command
from miner_scanner.models import Credentials
from miner_scanner.repository import DeviceRepository
from miner_scanner.service import ScannerService
from tests.fakes import FakeFactory
from tests.test_stock_compatibility import WebTransport
from tests.test_stock_mode_timeout import device


class KS5ModeTests(unittest.TestCase):
    def setUp(self):
        catalog = deepcopy(compat.interfaces())
        self.assets, self.pages = {}, {}
        for digest, definition in list(catalog['miner'].items()):
            name = definition['source_asset']
            if name not in ('miner.6b9cb7.js', 'miner.0fb1b6.js'):
                continue
            # Never execute or redistribute vendor JavaScript in test fixtures.
            body = ('/* synthetic reviewed ' + name + ' */').encode()
            selector = '<select v-model="minerForm[\'miner-mode\']"></select>' if name == 'miner.6b9cb7.js' else ''
            page = (selector + '<script src="/js/' + name + '"></script>').encode()
            self.assets[name], self.pages[name] = body, page
            definition['pages'] = [hashlib.sha256(page).hexdigest()]
            del catalog['miner'][digest]
            catalog['miner'][hashlib.sha256(body).hexdigest()] = definition
        self.addCleanup(patch.stopall)
        patch.object(compat, 'interfaces', return_value=catalog).start()

    def service(self, asset='miner.6b9cb7.js', mode='0'):
        data = device('Antminer KS5', 'arbitrary firmware version')
        data['config'] = {
            'pools': [{'url': 'stratum+tcp://pool.example.invalid:3333', 'user': 'demo.1', 'pass': ''},
                      {'url': '', 'user': ''}, {'url': '', 'user': '', 'pass': ''}],
            'algo': 'ks5_2382', 'bitmain-fan-ctrl': True, 'bitmain-fan-pwm': '85',
            'bitmain-freq': '500', 'bitmain-voltage': '1300',
            'bitmain-work-mode': mode, 'bitmain-freq-level': '100',
        }
        factory = FakeFactory(data)
        factory.apply_mode = False
        factory.data['/cgi-bin/get_blink_status.cgi'] = {'blink': False}
        factory.web = {'/miner.html': self.pages[asset], '/js/' + asset: self.assets[asset]}
        repository = DeviceRepository(':memory:')
        self.addCleanup(repository.close)
        service = ScannerService(repository=repository,
            transport_factory=lambda *args: WebTransport(factory, *args))
        service.set_default_credentials(Credentials('root', 'fixture-password'))
        record = service.poll('192.0.2.5')
        return service, factory, record

    def test_visible_sleep_normal_uses_reviewed_mapping_and_preserves_settings(self):
        for mode in ('0', 0):
            with self.subTest(mode_type=type(mode).__name__):
                service, factory, record = self.service(mode=mode)
                baseline = deepcopy(factory.data['config'])
                self.assertEqual(record.capabilities['mining_stop'], 'supported')
                self.assertEqual(record.capabilities['mining_start'], 'supported')
                self.assertEqual(record.capabilities['normal_power'], 'supported')
                for action, target in [('sleep', 1), ('wakeup', 0)]:
                    result = execute_command(service, '192.0.2.5', action)
                    self.assertEqual(result.status, 'succeeded', result.message)
                    self.assertEqual(factory.writes[-1], ('/cgi-bin/set_miner_conf.cgi', 'POST', {
                        'pools': baseline['pools'], 'bitmain-fan-ctrl': True,
                        'bitmain-fan-pwm': '85', 'freq-level': '100', 'miner-mode': target,
                    }))
                for key in baseline.keys() - {'bitmain-work-mode'}:
                    self.assertEqual(factory.data['config'][key], baseline[key])
                self.assertEqual(len(factory.writes), 2)
                self.assertFalse(record.display['ControlCompatibility']['hardware_verified_commands'])
                for action in ('low', 'hem'):
                    self.assertEqual(execute_command(service, '192.0.2.5', action,
                                                     allow_unverified=True).status, 'unsupported')
                self.assertEqual(len(factory.writes), 2)

    def test_page_without_mode_selector_does_not_inherit_sleep_from_same_read_schema(self):
        service, factory, record = self.service('miner.0fb1b6.js')
        for action in ('sleep', 'wakeup', 'normal_power', 'low', 'hem'):
            self.assertEqual(execute_command(service, '192.0.2.5', action,
                                             allow_unverified=True).status, 'unsupported')
        self.assertFalse(factory.writes)
        for action in ('led_on', 'led_off', 'reboot'):
            result = execute_command(service, '192.0.2.5', action)
            self.assertEqual(result.status, 'unconfirmed' if action == 'reboot' else 'succeeded')
        self.assertEqual(len(factory.writes), 3)

    def test_matching_script_with_a_changed_page_does_not_authorize_sleep(self):
        service, factory, _ = self.service()
        # Same JS payload code, but the HTML no longer exposes its selector.
        factory.web['/miner.html'] = b'<script src="/js/miner.6b9cb7.js"></script>'
        record = service.poll('192.0.2.5', force_identify=True)
        self.assertEqual(record.display['ControlCompatibility']['interfaces']['mode_basis'], 'unconfirmed_ui_page')
        self.assertEqual(execute_command(service, '192.0.2.5', 'sleep', allow_unverified=True).status, 'unsupported')
        self.assertFalse(factory.writes)

    def test_unknown_or_missing_ui_does_not_use_ambiguous_config_fallback(self):
        for web in ({}, {'/miner.html': b'<script src="/js/miner.future.js"></script>',
                         '/js/miner.future.js': b'unknown firmware UI'}):
            service, factory, _ = self.service()
            factory.web = web
            self.assertEqual(execute_command(service, '192.0.2.5', 'sleep', allow_unverified=True).status, 'unsupported')
            self.assertFalse(factory.writes)

    def test_missing_config_field_still_blocks_mode_changes(self):
        service, factory, _ = self.service()
        del factory.data['config']['bitmain-freq-level']
        self.assertEqual(execute_command(service, '192.0.2.5', 'sleep').status, 'unsupported')
        self.assertFalse(factory.writes)

    def test_page_change_after_scan_is_rechecked_before_write(self):
        service, factory, record = self.service()
        self.assertEqual(record.capabilities['mining_stop'], 'supported')
        factory.web = {'/miner.html': self.pages['miner.0fb1b6.js'],
                       '/js/miner.0fb1b6.js': self.assets['miner.0fb1b6.js']}
        self.assertEqual(execute_command(service, '192.0.2.5', 'sleep').status, 'unsupported')
        self.assertFalse(factory.writes)
