"""The shared identity/inference transport stays on its fixed HTTPS origins."""
from threading import Event
import unittest
from unittest.mock import patch

from miner_scanner.ai.contracts import AnalysisError
from miner_scanner.ai.https_transport import request_bytes


class Response:
    status = 200
    def __init__(self, body=b'{}', status=200):
        self.body, self.status, self.closed = body, status, False
    def getheader(self, key, default=''):
        return {'Content-Type': 'application/json', 'Content-Encoding': 'identity'}.get(key, default)
    def read(self, count):
        result, self.body = self.body[:count], self.body[count:]
        return result
    def close(self):
        self.closed = True


class Connection:
    def __init__(self, reply):
        self.reply, self.closed, self.calls = reply, False, []
    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
    def getresponse(self):
        return self.reply
    def close(self):
        self.closed = True


class SharedHTTPTests(unittest.TestCase):
    def test_untrusted_origin_path_headers_and_limits_fail_before_network(self):
        with patch('miner_scanner.ai.https_transport.FixedHTTPSConnection') as connection:
            for host, path, headers in [('untrusted.invalid', '/', {}),
                    ('api.openai.com', '//untrusted.invalid/', {}),
                    ('api.openai.com', '/\r\nInjected', {}),
                    ('api.openai.com', '/', {'Authorization': 'synthetic\r\nInjected'})]:
                with self.assertRaises(AnalysisError):
                    request_bytes(host, 'POST', path, b'{}', headers)
            cancel = Event()
            cancel.set()
            with self.assertRaises(AnalysisError):
                request_bytes('api.openai.com', 'POST', '/v1/responses', b'{}', {}, cancel=cancel)
        connection.assert_not_called()

    def test_redirect_is_returned_to_caller_without_second_request(self):
        response = Response(status=302)
        client = Connection(response)
        with patch('miner_scanner.ai.https_transport.FixedHTTPSConnection', return_value=client) as factory:
            reply = request_bytes('auth.openai.com', 'GET', '/.well-known/openid-configuration', None, {})
        self.assertEqual(reply.status, 302)
        factory.assert_called_once()
        self.assertEqual(len(client.calls), 1)
        self.assertTrue(client.closed and response.closed)

    def test_bounded_body_and_sensitive_headers_not_returned(self):
        response = Response(b'{}')
        client = Connection(response)
        with patch('miner_scanner.ai.https_transport.FixedHTTPSConnection', return_value=client):
            reply = request_bytes('api.openai.com', 'GET', '/v1/models', None,
                                  {'Authorization': 'Bearer synthetic-secret'})
        self.assertNotIn('synthetic-secret', repr(reply))
        self.assertNotIn('authorization', reply.headers)
        self.assertEqual(client.calls[0][1]['headers']['Accept-Encoding'], 'identity')
        client = Connection(Response(b'123456'))
        with patch('miner_scanner.ai.https_transport.FixedHTTPSConnection', return_value=client):
            with self.assertRaises(AnalysisError) as caught:
                request_bytes('api.openai.com', 'GET', '/v1/models', None, {}, max_bytes=5)
        self.assertEqual(caught.exception.code, 'too_large')
