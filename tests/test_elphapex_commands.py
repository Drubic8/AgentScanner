import unittest
from unittest.mock import Mock

from miner_scanner.commands import _http_accept, elphapex


class ElphapexCommandTests(unittest.TestCase):
    def transport(self, body=b'{"stats":"success","msg":"OK!","code":"M000"}'):
        transport = Mock()
        transport.http.return_value = (200, body)
        return transport

    def test_manufacturer_success_code_is_scoped_and_errors_still_fail(self):
        transport = self.transport()
        self.assertFalse(_http_accept(transport, '/other-vendor'))
        self.assertTrue(elphapex(transport, None, 'identify_on', None)[0])
        for body in (b'{"code":"M001"}', b'{"code":"M000","success":false}',
                     b'{"code":"M000","error":"denied"}'):
            with self.subTest(body=body):
                self.assertFalse(elphapex(self.transport(body), None, 'identify_on', None)[0])

    def test_led_readback_requires_matching_boolean(self):
        for action, expected in [('identify_on', True), ('identify_off', False)]:
            transport = self.transport()
            _, verify = elphapex(transport, None, action, None)
            for response, success in [({'blink': expected}, True),
                                      ({'blink': not expected}, False), ({}, False),
                                      ({'blink': str(expected)}, False)]:
                transport.http_json.return_value = response
                self.assertEqual(verify(), success)

    def test_sleep_and_wake_readback(self):
        for action, mode in [('mining_stop', '-1000'), ('mining_start', '0')]:
            transport = self.transport()
            accepted, verify = elphapex(transport, None, action, None)
            self.assertTrue(accepted)
            self.assertEqual(transport.http.call_args.kwargs['payload'], {'workmode': mode})
            transport.http_json.return_value = {'fc-work-mode': int(mode)}
            self.assertTrue(verify())
            transport.http_json.return_value = {}
            self.assertFalse(verify())

    def test_reboot_uses_get_without_payload_and_remains_unconfirmed(self):
        transport = self.transport(b'')
        accepted, verify = elphapex(transport, None, 'reboot', None)
        self.assertTrue(accepted)
        self.assertIsNone(verify)
        self.assertEqual(transport.http.call_args.args, ('/cgi-bin/luci/reboot.cgi', 'GET'))
        self.assertIsNone(transport.http.call_args.kwargs['payload'])
