"""Firmware identity is independent of telemetry and JSON key order."""
from copy import deepcopy
import unittest

from miner_scanner.drivers import make_record
from miner_scanner.profiles import ProfileRegistry
from tests.test_l9_stock_commands import l9


class AntminerIdentityTests(unittest.TestCase):
    def setUp(self):
        self.data = l9()
        self.data['system']['macaddr'] = '02:00:00:00:00:09'
        self.data['stats']['STATS'].append({'miner_version': '86.48-2.0.0'})
        self.profile = ProfileRegistry().resolve(self.data)

    def record(self, data=None, previous=None):
        return make_record(self.profile, '192.0.2.9', self.data if data is None else data, previous)

    def test_key_order_and_program_version_do_not_change_identity(self):
        before = self.record()
        reordered = {key: self.data[key] for key in reversed(self.data)}
        after = self.record(reordered, before)
        self.assertEqual(after.identity.fingerprint, before.identity.fingerprint)
        self.data['stats']['STATS'][1]['miner_version'] = 'another mining-program version'
        changed = self.record(previous=before)
        self.assertEqual(changed.identity.fingerprint, before.identity.fingerprint)
        self.assertEqual(changed.identity.device_id, before.identity.device_id)

    def test_real_firmware_change_in_either_endpoint_changes_fingerprint(self):
        self.data['version']['VERSION'][0]['firmware_version'] = 'explicit-fw-1'
        before = self.record()
        for endpoint in ('version', 'system'):
            data = deepcopy(self.data)
            if endpoint == 'version':
                data['version']['VERSION'][0]['firmware_version'] = 'explicit-fw-2'
            else:
                data['system']['system_filesystem_version'] = 'another image build'
            self.assertNotEqual(self.record(data, before).identity.fingerprint, before.identity.fingerprint)

    def test_missing_authoritative_build_does_not_reuse_program_version(self):
        before = self.record()
        del self.data['system']['system_filesystem_version']
        after = self.record(previous=before)
        self.assertIsNone(after.identity.firmware_version)
        self.assertNotEqual(after.identity.fingerprint, before.identity.fingerprint)

    def test_mac_change_remains_a_device_change(self):
        before = self.record()
        self.data['system']['macaddr'] = '02:00:00:00:00:10'
        after = self.record(previous=before)
        self.assertNotEqual(after.identity.fingerprint, before.identity.fingerprint)
        self.assertNotEqual(after.identity.device_id, before.identity.device_id)

    def test_system_variant_is_stable_when_cgminer_reports_generic_family(self):
        self.data['version']['VERSION'][0]['Type'] = 'Antminer KS5'
        self.data['stats']['STATS'][0]['Type'] = 'Antminer KS5'
        self.data['system']['minertype'] = 'Antminer KS5 Pro'
        before = self.record()
        self.assertEqual(before.identity.model, 'Antminer KS5 Pro')
        self.assertEqual(before.display['Model'], 'Antminer KS5 Pro')
        reordered = {key: self.data[key] for key in reversed(self.data)}
        after = self.record(reordered, before)
        self.assertEqual(after.identity.fingerprint, before.identity.fingerprint)
        self.assertEqual(after.identity.device_id, before.identity.device_id)
        self.data['stats']['STATS'][0]['Type'] = 'Antminer KS5 Pro'
        self.assertEqual(self.record(previous=before).identity.fingerprint, before.identity.fingerprint)

    def test_real_system_model_change_is_not_hidden_by_generic_cgminer_model(self):
        before = self.record()
        self.data['system']['minertype'] = 'Antminer L7'
        after = self.record(previous=before)
        self.assertEqual(after.identity.model, 'Antminer L7')
        self.assertNotEqual(after.identity.fingerprint, before.identity.fingerprint)
        self.assertNotEqual(after.identity.device_id, before.identity.device_id)

    def test_cgminer_api_loss_during_restart_does_not_replace_known_http_device(self):
        before=self.record()
        for api in (None,'another CGMiner API'):
            with self.subTest(api=api):
                self.data['version']['VERSION'][0]['API']=api
                after=self.record(previous=before)
                self.assertEqual(after.identity.fingerprint,before.identity.fingerprint)
                self.assertEqual(after.identity.device_id,before.identity.device_id)
                self.assertEqual(after.identity.api_version,api)

    def test_api_change_is_not_ignored_without_current_hardware_id_or_build(self):
        for field in ('macaddr','system_filesystem_version'):
            with self.subTest(missing=field):
                data=deepcopy(self.data)
                del data['system'][field]
                before=self.record(data)
                data['version']['VERSION'][0]['API']=None
                self.assertNotEqual(self.record(data,before).identity.fingerprint,before.identity.fingerprint)
