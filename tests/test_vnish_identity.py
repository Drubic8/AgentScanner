"""VNish metadata is independent of telemetry ordering; no network/device writes."""
from copy import deepcopy
from itertools import permutations
import unittest
from unittest.mock import patch

from miner_scanner.commands import execute_command
from miner_scanner.drivers import make_record
from miner_scanner.profiles import ProfileRegistry
from tests import test_vnish_compatibility as vnish_tests


def metadata():
    return {
        'version': {'VERSION': [{'API': '3.7'}]},
        'stats': {'STATS': [{'Type': 'Antminer L9 (Vnish 1.2.7)', 'miner_version': 'cg-software',
                            'boards': [{'serial': 'board-fixture'}]}]},
        'system': {'model': 'stock-model-alias', 'firmware_version': 'stock-image-date',
                   'serial': 'stock-serial-alias', 'mac': '00:00:00:00:00:02'},
        'vnish_info': {'model': 'l9', 'miner': 'Antminer L9', 'fw_name': 'Vnish',
                       'fw_version': '1.2.7', 'serial': 'miner-fixture', 'hr_measure': 'MH/s',
                       'system': {'network_status': {'mac': '00:00:00:00:00:01'}}},
        'vnish_summary': {'miner': {'hr_realtime': 17000,
                                    'miner_status': {'miner_state': 'mining'}}},
    }


class VnishIdentityTests(unittest.TestCase):
    def setUp(self):
        self.profile = ProfileRegistry().by_id['bitmain.vnish']

    def record(self, data, previous=None):
        return make_record(self.profile, '192.0.2.1', data, previous)

    def test_all_endpoint_orders_have_same_identity(self):
        data = metadata()
        original = self.record(data)
        self.assertEqual(original.identity.model, 'l9')
        self.assertEqual(original.identity.firmware_version, '1.2.7')
        self.assertEqual(original.identity.serial, 'miner-fixture')
        self.assertEqual(original.identity.mac, '00:00:00:00:00:01')
        for order in permutations(data):
            with self.subTest(order=order):
                current = self.record({key: data[key] for key in order}, original)
                self.assertEqual(current.identity.fingerprint, original.identity.fingerprint)
                self.assertEqual(current.identity.device_id, original.identity.device_id)

    def test_cgminer_disappears_or_display_type_changes_without_changing_hardware(self):
        original = self.record(metadata())
        for mode in ('missing', 'renamed'):
            data = metadata()
            if mode == 'missing':
                data.pop('version'); data.pop('stats')
            else:
                data['stats']['STATS'][0]['Type'] = 'L9'
                data['version']['VERSION'][0]['API'] = '4.0'
            current = self.record(data, original)
            self.assertEqual(current.identity.fingerprint, original.identity.fingerprint)
            self.assertEqual(current.identity.device_id, original.identity.device_id)

    def test_real_hardware_and_firmware_changes_remain_visible(self):
        original = self.record(metadata())
        for field in ('model', 'fw_version', 'serial', 'mac'):
            with self.subTest(field=field):
                data = metadata()
                if field == 'mac':
                    data['vnish_info']['system']['network_status']['mac'] = '00:00:00:00:00:03'
                else:
                    data['vnish_info'][field] = 'different-device-or-build'
                self.assertNotEqual(self.record(data, original).identity.fingerprint, original.identity.fingerprint)

    def test_missing_http_identity_never_uses_hashboard_or_stock_fields(self):
        for info in (None, {}, {'system': {'network_status': None}}):
            data = metadata(); data['vnish_info'] = info
            current = self.record(data)
            self.assertIsNone(current.identity.serial)
            self.assertIsNone(current.identity.mac)
            self.assertIsNone(current.identity.firmware_version)


class VnishPreflightIdentityTests(unittest.TestCase):
    def setUp(self):
        # Reuse the reviewed synthetic REST contract setup, without inheriting
        # or re-running the unrelated compatibility test suite.
        self.fixture = vnish_tests.VnishCompatibilityTests('test_locate_on_off_use_same_bearer_token_and_preserve_mining')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.service, self.factory = self.fixture.service, self.fixture.factory
        self.factory.data.update(metadata())
        self.original = self.service.poll('192.0.2.1', force_identify=True)

    def test_led_is_sent_once_when_only_discovery_order_changes(self):
        # Cached REST-first input and discovery TCP-first input used to disagree.
        data = metadata()
        original = make_record(self.service.registry.by_id['bitmain.vnish'], '192.0.2.1',
                               {key: data[key] for key in reversed(data)}, self.original)
        with patch.object(self.service, 'get_record', return_value=original):
            result = execute_command(self.service, '192.0.2.1', 'led_on', device_id=original.identity.device_id)
        self.assertEqual(result.status, 'succeeded')
        self.assertEqual(len(self.factory.writes), 1)

    def test_real_identity_change_prevents_any_led_write(self):
        for field in ('serial', 'fw_version', 'model', 'mac'):
            with self.subTest(field=field):
                self.factory.data.update(metadata())
                self.service._records['192.0.2.1'] = self.original
                if field == 'mac':
                    self.factory.data['vnish_info']['system']['network_status']['mac'] = '00:00:00:00:00:03'
                else:
                    self.factory.data['vnish_info'][field] = 'different-device-or-build'
                result = execute_command(self.service, '192.0.2.1', 'led_on', device_id=self.original.identity.device_id)
                self.assertEqual(result.status, 'skipped')
                self.assertFalse(self.factory.writes)
