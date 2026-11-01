"""Offline contracts for read-only log routes, auth boundaries and bounded files."""
from pathlib import Path
import tempfile
from threading import Event
import unittest

import requests

from miner_scanner.access import AccessProfile, AccessProfiles
from miner_scanner.device_logs import (MAX_LOG_BYTES, DeviceLogResult,
    load_log_file, read_device_log, supported_log_kinds)
from miner_scanner.runtime import AuthenticationError, ProtocolError


STOCK = {'IP': '192.0.2.20', 'ProfileId': 'bitmain.stock.s21.fr112'}
VNISH = {'IP': '192.0.2.21', 'ProfileId': 'bitmain.vnish'}
NO_ACCESS = AccessProfiles([])


class ScriptedFactory:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.instances = []

    def __call__(self, ip, operation, credentials):
        owner = self

        class FakeTransport:
            def __init__(self):
                self.operation = operation
                self.closed = False

            def __enter__(self):
                return self

            def __exit__(self, *_):
                self.closed = True

            def http(self, path, method='GET', **kwargs):
                operation.remaining()
                owner.calls.append((ip, path, method, credentials, kwargs))
                if not owner.responses:
                    raise AssertionError('Unexpected request')
                response = owner.responses.pop(0)
                if isinstance(response, Exception):
                    raise response
                return response

        transport = FakeTransport()
        self.instances.append(transport)
        return transport


class DeviceLogReadTests(unittest.TestCase):
    def read(self, row=STOCK, kind='miner', responses=(), profiles=NO_ACCESS, **kwargs):
        factory = ScriptedFactory(*responses)
        result = read_device_log(row, kind, access_profiles=profiles, transport_factory=factory, **kwargs)
        return result, factory

    def test_profile_not_model_controls_adapter_availability(self):
        self.assertEqual(supported_log_kinds(STOCK), ('miner', 'system'))
        self.assertEqual(supported_log_kinds(VNISH), ('miner', 'system'))
        for row in ({'IP': '192.0.2.20', 'Model': 'Antminer S21'},
                    dict(STOCK, ProfileId='bitmain.pitbit'),
                    dict(STOCK, ProfileId='canaan.avalon'),
                    dict(STOCK, Identity={'profile_id': 'bitmain.vnish'})):
            result, factory = self.read(row)
            self.assertEqual(result.status, 'unsupported')
            self.assertEqual(factory.calls, [])

    def test_rejects_non_ipv4_arbitrary_url_and_identity_mismatch(self):
        for row in (dict(STOCK, IP='http://192.0.2.20/'), dict(STOCK, IP='localhost'),
                    dict(STOCK, IP='::1'), dict(STOCK, Identity={'ip': '192.0.2.22'})):
            result, factory = self.read(row)
            self.assertEqual(result.status, 'unsupported')
            self.assertEqual(factory.calls, [])

    def test_unknown_kind_never_becomes_a_path(self):
        result, factory = self.read(VNISH, '../../system/reboot')
        self.assertEqual(result.status, 'unsupported')
        self.assertEqual(factory.calls, [])

    def test_stock_current_log_plain_text_and_size_budget(self):
        result, factory = self.read(responses=[(200, b'fan 1 lost\r\nchain 2 failure\n')])
        self.assertEqual(result.status, 'succeeded')
        self.assertEqual(result.text, 'fan 1 lost\nchain 2 failure\n')
        self.assertEqual(result.source, '/cgi-bin/log.cgi')
        self.assertEqual(factory.calls[0][2], 'GET')
        self.assertEqual(factory.calls[0][4], {'response_limit': MAX_LOG_BYTES})
        self.assertEqual(factory.instances[0].operation.options.device_timeout, 20.0)
        self.assertTrue(factory.instances[0].closed)
        self.assertFalse(result.truncated)
        self.assertIsNotNone(result.collected_at)

    def test_stock_missing_modern_route_falls_back_only_to_legacy_read(self):
        result, factory = self.read(responses=[(404, b''), (200, b'Linux version 4.9\n')])
        self.assertEqual(result.status, 'succeeded')
        self.assertEqual(result.source, '/cgi-bin/get_kernel_log.cgi')
        self.assertEqual([call[1] for call in factory.calls],
                         ['/cgi-bin/log.cgi', '/cgi-bin/get_kernel_log.cgi'])
        self.assertTrue(all(call[2] == 'GET' for call in factory.calls))

    def test_stock_system_log_reads_official_kernel_route_only(self):
        result, factory = self.read(kind='system', responses=[(200, b'[0.003] kernel started\n')])
        self.assertEqual(result.kind, 'system')
        self.assertEqual([call[1] for call in factory.calls], ['/cgi-bin/get_kernel_log.cgi'])

    def test_http_error_or_login_html_does_not_trigger_route_fanout(self):
        for response, expected in (((500, b'failure'), 'unavailable'),
                                   ((302, b''), 'unavailable'),
                                   ((200, b'<html><form>login</form></html>'), 'invalid_response'),
                                   ((200, b'{"error":"not authorized"}'), 'invalid_response')):
            result, factory = self.read(responses=[response])
            self.assertEqual(result.status, expected)
            self.assertEqual(len(factory.calls), 1)
            self.assertEqual(result.text, '')

    def test_missing_stock_routes_are_unsupported(self):
        result, factory = self.read(responses=[(404, b''), (404, b'')])
        self.assertEqual(result.status, 'unsupported')
        self.assertEqual(len(factory.calls), 2)

    def test_stock_credentials_use_specific_scope_first(self):
        profiles = AccessProfiles([
            AccessProfile('general', 'antminer', 'root', 'general'),
            AccessProfile('specific', 'antminer', 'root', 'specific', targets='192.0.2.20'),
            AccessProfile('wrong family', 'vnish', '', 'vnish-password')])
        result, factory = self.read(responses=[AuthenticationError('private'), (200, b'ok')], profiles=profiles)
        self.assertEqual(result.status, 'succeeded')
        self.assertEqual([call[3].password for call in factory.calls], ['specific', 'general'])
        self.assertIs(factory.instances[0].operation, factory.instances[1].operation)
        self.assertTrue(all(transport.closed for transport in factory.instances))

    def test_explicit_empty_or_disabled_profiles_never_restore_defaults(self):
        for profiles in (NO_ACCESS, AccessProfiles([
                AccessProfile('disabled', 'antminer', 'root', 'private', enabled=False)])):
            result, factory = self.read(responses=[AuthenticationError('private')], profiles=profiles)
            self.assertEqual(result.status, 'auth_required')
            self.assertEqual(len(factory.calls), 1)
            self.assertIsNone(factory.calls[0][3])

    def test_vnish_public_read_uses_no_http_auth_and_no_unlock(self):
        profiles = AccessProfiles([AccessProfile('VNish', 'vnish', '', 'secret')])
        result, factory = self.read(VNISH, responses=[(200, b'miner started')], profiles=profiles)
        self.assertEqual(result.status, 'succeeded')
        self.assertEqual(factory.calls[0][1:4], ('/api/v1/logs/miner', 'GET', None))
        self.assertEqual(len(factory.calls), 1)

    def test_vnish_auth_only_unlocks_and_reads_requested_log(self):
        profiles = AccessProfiles([AccessProfile('VNish', 'vnish', '', 'secret')])
        result, factory = self.read(VNISH, 'system', profiles=profiles,
            responses=[AuthenticationError('private'), (200, b'{"token":"abc123"}'), (200, b'booted')])
        self.assertEqual(result.status, 'succeeded')
        self.assertEqual([(call[1], call[2]) for call in factory.calls],
            [('/api/v1/logs/system', 'GET'), ('/api/v1/unlock', 'POST'), ('/api/v1/logs/system', 'GET')])
        self.assertTrue(all(call[3] is None for call in factory.calls))
        self.assertEqual(factory.calls[1][4]['payload'], {'pw': 'secret'})
        self.assertEqual(factory.calls[2][4]['headers'], {'Authorization': 'Bearer abc123'})
        self.assertNotIn('secret', repr(result))
        self.assertNotIn('abc123', repr(result))

    def test_vnish_disabled_credentials_do_not_unlock(self):
        profiles = AccessProfiles([AccessProfile('VNish', 'vnish', '', 'secret', enabled=False)])
        result, factory = self.read(VNISH, responses=[(401, b'')], profiles=profiles)
        self.assertEqual(result.status, 'auth_required')
        self.assertEqual(len(factory.calls), 1)

    def test_vnish_wrong_password_tries_next_but_rate_limit_does_not(self):
        profiles = AccessProfiles([AccessProfile('one', 'vnish', '', 'one'), AccessProfile('two', 'vnish', '', 'two')])
        result, factory = self.read(VNISH, profiles=profiles, responses=[(401, b''), (403, b''),
            (200, b'{"token":"valid"}'), (200, b'chain 1 ready')])
        self.assertEqual(result.status, 'succeeded')
        self.assertEqual([call[4]['payload']['pw'] for call in factory.calls if call[2] == 'POST'], ['one', 'two'])
        result, factory = self.read(VNISH, profiles=profiles, responses=[(401, b''), (429, b'')])
        self.assertEqual(result.status, 'unavailable')
        self.assertEqual(len(factory.calls), 2)

    def test_vnish_malformed_token_and_forbidden_read_do_not_retry_password(self):
        profiles = AccessProfiles([AccessProfile('one', 'vnish', '', 'one'), AccessProfile('two', 'vnish', '', 'two')])
        for payload in (b'{"token":42}', b'{"token":"bad\\r\\nheader"}', b'<html>failure</html>'):
            result, factory = self.read(VNISH, profiles=profiles, responses=[(401, b''), (200, payload)])
            self.assertEqual(result.status, 'invalid_response')
            self.assertEqual(len(factory.calls), 2)
        result, factory = self.read(VNISH, profiles=profiles,
            responses=[(401, b''), (200, b'{"token":"good"}'), (403, b'')])
        self.assertEqual(result.status, 'auth_required')
        self.assertEqual(len(factory.calls), 3)

    def test_binary_and_over_limit_responses_are_not_logs(self):
        for response, expected in (((200, b'abc\x00def'), 'invalid_response'),
                ((200, bytes([255]) * 100), 'invalid_response'),
                ((200, b'x' * (MAX_LOG_BYTES + 1)), 'too_large'),
                (ProtocolError('HTTP response too large'), 'too_large')):
            result, _ = self.read(responses=[response])
            self.assertEqual(result.status, expected)
            self.assertFalse(result.text)

    def test_cancelled_before_start_makes_no_connection(self):
        cancel = Event()
        cancel.set()
        result, factory = self.read(cancel=cancel)
        self.assertEqual(result.status, 'cancelled')
        self.assertEqual(factory.instances, [])

    def test_connection_and_timeout_exceptions_never_leak_secrets(self):
        for exc, expected in ((requests.Timeout('secret password'), 'timeout'),
                (requests.ConnectionError('http://private:token@host'), 'unavailable'),
                (RuntimeError('super secret'), 'error')):
            result, _ = self.read(responses=[exc])
            self.assertEqual(result.status, expected)
            self.assertNotIn(str(exc), repr(result))

    def test_empty_log_is_successful_and_utf8_replacement_is_bounded(self):
        result, _ = self.read(responses=[(200, b'')])
        self.assertEqual((result.status, result.text), ('succeeded', ''))
        result, _ = self.read(responses=[(200, b'chain 1 error \xff\n')])
        self.assertEqual(result.status, 'succeeded')
        self.assertIn('chain 1 error', result.text)


class LogFileTests(unittest.TestCase):
    def test_import_file_has_no_full_local_path_and_handles_utf16(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'device.log'
            path.write_bytes('Ошибка вентилятора\r\n'.encode('utf-16'))
            result = load_log_file(path)
            self.assertEqual(result.status, 'succeeded')
            self.assertEqual(result.kind, 'imported')
            self.assertEqual(result.source, 'device.log')
            self.assertEqual(result.text, 'Ошибка вентилятора\n')
            self.assertNotIn(directory, repr(result))

    def test_import_size_binary_missing_and_html_fail_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'device.log'
            self.assertEqual(load_log_file(path).status, 'unavailable')
            for body, status in ((b'x' * (MAX_LOG_BYTES + 1), 'too_large'),
                    (b'PK\x03\x04 archive', 'invalid_response'),
                    (b'<html>login</html>', 'invalid_response')):
                path.write_bytes(body)
                self.assertEqual(load_log_file(path).status, status)

    def test_import_preserves_json_lines_as_plain_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'device.jsonl'
            path.write_text('{"event":"fan lost"}\n', encoding='utf-8')
            result = load_log_file(path)
            self.assertEqual(result.status, 'succeeded')
            self.assertEqual(result.text, '{"event":"fan lost"}\n')


if __name__ == '__main__':
    unittest.main()
