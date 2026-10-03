from copy import deepcopy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import requests

from miner_scanner.commands import vnish
from miner_scanner.drivers import make_record
from miner_scanner.models import Credentials
from miner_scanner.normalization import normalize
from miner_scanner.profiles import ProfileRegistry
from miner_scanner.runtime import Operation
from miner_scanner.transports import Transport


class ResponseBytes(io.BytesIO):
    def read1(self, size=-1, **kwargs):
        return super().read1(size)


class VnishRegressionTests(unittest.TestCase):
    def test_actual_l9_rates_use_rest_measure_on_both_firmwares(self):
        fixtures = json.loads((Path(__file__).parent / 'fixtures/vnish_l9_hardware.json').read_text())
        for fixture in fixtures:
            with self.subTest(version=fixture['info']['fw_version']):
                data = {'vnish_info': fixture['info'], 'vnish_summary': fixture['summary'],
                        'stats': {'STATS': [{'Type': 'Antminer L9 (Vnish)', 'GHS 5s': 16000}]}}
                profile = ProfileRegistry().resolve(data)
                record = make_record(profile, '192.0.2.1', data)
                expected = fixture['summary']['miner']['hr_realtime'] * 1e6
                self.assertEqual(record.telemetry.rate, expected)
                self.assertEqual(record.display['Real'], f'{expected / 1e9:.2f} GH/s')
                self.assertEqual(record.display['Model'], 'Antminer L9 (Vnish)')

    def test_units_zero_missing_and_sha_rates_without_magnitude_guessing(self):
        for unit, multiplier in [('MH/s', 1e6), ('GH/s', 1e9)]:
            for rate in (0, 1, 16000, 250000):
                data = {'vnish_info': {'hr_measure': unit},
                        'vnish_summary': {'miner': {'hr_realtime': rate, 'hr_average': rate}},
                        'summary': {'SUMMARY': [{'GHS 5s': 123456789}]}}
                result = normalize(data, {'Algo': 'Scrypt' if unit == 'MH/s' else 'SHA-256'}, 'vnish')
                self.assertEqual(result.rate, rate * multiplier)
                self.assertEqual(result.average_rate, rate * multiplier)
                missing = deepcopy(data)
                del missing['vnish_info']['hr_measure']
                self.assertIsNone(normalize(missing, {}, 'vnish').rate)
        data = {'vnish_info': {'hr_measure': 'MH/s'}, 'vnish_summary': {'miner': {'instant_hashrate': 16}}}
        self.assertIsNone(normalize(data, {}, 'vnish').rate)

    def test_explicit_token_survives_basic_and_digest_session_auth(self):
        for scheme in ('basic', 'digest'):
            with Transport('192.0.2.1', Operation(), Credentials('user', 'test-password', scheme)) as transport:
                seen = []
                def respond(request, **kwargs):
                    seen.append(request)
                    response = requests.Response()
                    response.status_code = 200
                    response.raw = ResponseBytes(b'{}')
                    response.request = request
                    return response
                with patch.object(transport.session, 'send', side_effect=respond):
                    transport.http_json('/api/v1/settings', headers={'Authorization': 'Bearer test-token'})
                    transport.http_json('/api/v1/settings', headers={'x-api-key': 'test-api-key'})
                    transport.http_json('/cgi-bin/status.cgi')
                self.assertEqual(seen[0].headers['Authorization'], 'Bearer test-token')
                self.assertFalse(seen[0].hooks['response'])
                self.assertNotIn('Authorization', seen[1].headers)
                self.assertFalse(seen[1].hooks['response'])
                if scheme == 'basic':
                    self.assertTrue(seen[2].headers['Authorization'].startswith('Basic '))
                else:
                    self.assertTrue(seen[2].hooks['response'])

    def test_vnish_command_and_readback_use_same_bearer_token(self):
        for action, state in [('mining_stop', 'stopped'), ('mining_start', 'mining')]:
            transport = Mock()
            transport.http_json.side_effect = [{'token': 'test-token'}, {'miner': {'miner_status': {'miner_state': state}}}]
            transport.http.return_value = (200, b'')
            accepted, verify = vnish(transport, None, action, Credentials('', 'test-password'))
            self.assertTrue(accepted)
            self.assertTrue(verify())
            self.assertEqual(transport.http.call_args.kwargs['headers'], {'Authorization': 'Bearer test-token'})
            self.assertEqual(transport.http_json.call_args.kwargs['headers'], {'Authorization': 'Bearer test-token'})

    def test_led_requires_matching_status_not_only_http_acceptance(self):
        for action, expected in [('identify_on', True), ('identify_off', False)]:
            for state in (expected, not expected, None, str(expected)):
                transport = Mock()
                transport.http_json.side_effect = [{'token': 'test-token'},
                                                  {'find_miner': not expected}, {'find_miner': state}]
                transport.http.return_value = (200, b'null')
                accepted, verify = vnish(transport, None, action, Credentials('', 'test-password'))
                self.assertTrue(accepted)
                self.assertEqual(verify(), state is expected)
                self.assertEqual(transport.http_json.call_args.args, ('/api/v1/status',))
