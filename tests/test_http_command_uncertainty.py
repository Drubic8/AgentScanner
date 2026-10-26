"""A control HTTP 5xx never proves that replaying a write would be safe."""
from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

from miner_scanner.batch import execute_batch
from miner_scanner.commands import _http_accept, execute_command
from miner_scanner.preflight import is_retry_safe
from tests import test_vnish_compatibility as fixtures


class HttpAcceptanceTests(unittest.TestCase):
    def test_all_server_error_responses_are_uncertain_with_either_legacy_keyword(self):
        transport = Mock()
        for status in (500, 501, 502, 503, 504, 599):
            for legacy_option in (False, True):
                with self.subTest(status=status, legacy_option=legacy_option):
                    transport.http.return_value = status, b'{"success":false}'
                    self.assertIsNone(_http_accept(transport, '/control', verify_server_error=legacy_option))

    def test_success_and_explicit_rejection_remain_distinct(self):
        transport = Mock()
        for status, body, expected in (
            (200, b'{}', True), (204, b'', True),
            (400, b'{}', False), (403, b'{}', False), (404, b'{}', False),
            (200, b'{"success":false}', False),
            (200, b'{"code":"denied"}', False),
        ):
            with self.subTest(status=status, body=body):
                transport.http.return_value = status, body
                self.assertIs(_http_accept(transport, '/control'), expected)


class VnishServerErrorTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.VnishCompatibilityTests(
            'test_locate_on_off_use_same_bearer_token_and_preserve_mining')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.service, self.factory = self.fixture.service, self.fixture.factory
        self.factory.data['/docs/api-doc.json'] = fixtures.api_spec(both=True, core=True)
        self.service.poll('192.0.2.1', force_identify=True)

    def response(self, path, *, apply, status=500, body=b'{"success":false}'):
        original = fixtures.VnishTransport.http

        def respond(transport, request_path, method='GET', **kwargs):
            if request_path == path and method == 'POST':
                if apply:
                    original(transport, request_path, method, **kwargs)
                else:
                    self.factory.writes.append((request_path, method,
                        deepcopy(kwargs.get('payload')), kwargs.get('headers')))
                return status, body
            return original(transport, request_path, method, **kwargs)
        return patch.object(fixtures.VnishTransport, 'http', respond)

    def test_reboot_500_is_uncertain_even_when_device_may_have_applied_it(self):
        for apply in (False, True):
            with self.subTest(apply=apply):
                self.factory.writes.clear()
                command_id = f'reboot-500-applied-{apply}'
                with self.response('/api/v1/system/reboot', apply=apply):
                    result = execute_command(self.service, '192.0.2.1', 'reboot', command_id=command_id)
                    repeated = execute_command(self.service, '192.0.2.1', 'reboot', command_id=command_id)
                self.assertEqual(result.status, 'unconfirmed')
                self.assertFalse(result.accepted_by_api)
                self.assertFalse(is_retry_safe(result))
                self.assertEqual(repeated, result)
                self.assertEqual(len(self.factory.writes), 1)

    def test_led_500_is_resolved_only_by_readback_and_never_replayed(self):
        for action, expected in (('led_on', True), ('led_off', False)):
            for apply in (False, True):
                with self.subTest(action=action, apply=apply):
                    self.factory.data['/api/v1/status']['find_miner'] = not expected
                    self.factory.writes.clear()
                    command_id = f'{action}-500-applied-{apply}'
                    with self.response('/api/v1/locate-miner', apply=apply):
                        result = execute_command(self.service, '192.0.2.1', action, command_id=command_id)
                        repeated = execute_command(self.service, '192.0.2.1', action, command_id=command_id)
                    self.assertEqual(result.status, 'succeeded' if apply else 'unconfirmed')
                    self.assertFalse(result.accepted_by_api)
                    self.assertFalse(is_retry_safe(result))
                    self.assertEqual(repeated, result)
                    self.assertEqual(len(self.factory.writes), 1)

    def test_explicit_http_or_json_rejection_remains_failed(self):
        for status, body in ((403, b'{}'), (200, b'{"success":false}')):
            with self.subTest(status=status, body=body):
                self.factory.data['/api/v1/status']['find_miner'] = False
                self.factory.writes.clear()
                with self.response('/api/v1/locate-miner', apply=False, status=status, body=body):
                    result = execute_command(self.service, '192.0.2.1', 'led_on')
                self.assertEqual(result.status, 'failed')
                self.assertEqual(result.message, 'API отклонил команду')
                self.assertTrue(is_retry_safe(result))
                self.assertEqual(len(self.factory.writes), 1)

    def test_batch_continues_after_500_and_never_offers_automatic_replay(self):
        rows = [self.service.poll(ip).to_legacy() for ip in ('192.0.2.1', '192.0.2.2')]
        original = fixtures.VnishTransport.http
        writes_by_ip = []

        def respond(transport, path, method='GET', **kwargs):
            if path == '/api/v1/system/reboot' and method == 'POST':
                writes_by_ip.append(transport.ip)
                response = original(transport, path, method, **kwargs)
                return (500, b'{}') if transport.ip == '192.0.2.1' else response
            return original(transport, path, method, **kwargs)

        with patch.object(fixtures.VnishTransport, 'http', respond):
            results = dict(execute_batch(self.service, [*rows, rows[0]], 'reboot'))
        self.assertEqual(len(results), 2)
        self.assertEqual(sorted(writes_by_ip), ['192.0.2.1', '192.0.2.2'])
        self.assertEqual(results['192.0.2.1'].status, 'unconfirmed')
        self.assertFalse(results['192.0.2.1'].accepted_by_api)
        self.assertEqual(results['192.0.2.2'].status, 'unconfirmed')
        self.assertTrue(results['192.0.2.2'].accepted_by_api)
        self.assertFalse(any(is_retry_safe(result) for result in results.values()))
