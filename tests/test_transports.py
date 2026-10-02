import json
import socket
import struct
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
import requests
from urllib3.exceptions import ProtocolError as RawProtocolError, ReadTimeoutError

from miner_scanner.runtime import Cancelled, DeadlineExceeded, Operation, ProtocolError, ScanOptions
from miner_scanner.transports import Transport, recv_exact


class FragmentSocket:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.sent = b""
    def settimeout(self, timeout):
        pass
    def recv(self, size):
        if not self.chunks:
            return b""
        chunk = self.chunks.pop(0)
        if len(chunk) > size:
            self.chunks.insert(0, chunk[size:])
        return chunk[:size]
    def sendall(self, data):
        self.sent += data


class TransportTests(unittest.TestCase):
    def test_asset_limit_does_not_raise_json_or_tcp_limit(self):
        # A real 1.4 MiB VNish UI fits the asset budget, not the JSON budget.
        body = b'x' * 1_420_958
        with Transport('192.0.2.27', Operation()) as transport:
            response = MagicMock(status_code=200)
            response.__enter__.return_value = response
            with patch.object(transport.session, 'request', return_value=response):
                response.raw.read1.side_effect = [body, b'']
                with self.assertRaises(ProtocolError):
                    transport.http('/api/v1/summary')
                response.raw.read1.side_effect = [body, b'']
                self.assertEqual(transport.http('/assets/index-test.js',
                    response_limit=transport.operation.options.max_asset_bytes), (200, body))
                response.raw.read1.side_effect = [body, b'']
                with self.assertRaises(ProtocolError):
                    transport.http('/api/v1/summary')
                response.raw.read1.side_effect = [b'x' * 2_097_153, b'']
                with self.assertRaises(ProtocolError):
                    transport.http('/assets/index-test.js',
                        response_limit=transport.operation.options.max_asset_bytes)
            with self.assertRaises(ProtocolError):
                recv_exact(FragmentSocket([]), len(body), transport.operation)

    def test_invalid_asset_and_response_limits_fail_before_network(self):
        for value in (0, -1, True, 1.5, '2097152'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    ScanOptions(max_asset_bytes=value)
                with Transport('192.0.2.27', Operation()) as transport:
                    with patch.object(transport.session, 'request') as request:
                        with self.assertRaises(ValueError):
                            transport.http('/assets/index-test.js', response_limit=value)
                        request.assert_not_called()

    def test_interrupted_stream_is_a_normal_transport_error(self):
        for raw_error, expected in ((RawProtocolError('truncated response'), requests.ConnectionError),
                                    (ReadTimeoutError(None, '/config', 'slow body'), requests.ReadTimeout)):
            with self.subTest(error=type(raw_error).__name__), Transport('192.0.2.27', Operation()) as transport:
                response = MagicMock(status_code=200)
                response.raw.read1.side_effect = raw_error
                response.__enter__.return_value = response
                with patch.object(transport.session, 'request', return_value=response):
                    with self.assertRaises(expected):
                        transport.http('/cgi-bin/get_miner_conf.cgi')

    def test_fragmented_rpc_header_and_unicode_byte_length(self):
        response = b'{"code":0}'
        header = struct.pack("<I", len(response))
        sock = FragmentSocket([header[:1], header[1:3], header[3:], response[:3], response[3:]])
        with Transport("127.0.0.1", Operation()) as transport:
            self.assertEqual(transport.rpc_packet(sock, '{"cmd":"тест"}'), {"code": 0})
        self.assertEqual(struct.unpack("<I", sock.sent[:4])[0], len(sock.sent[4:]))

    def test_truncated_and_oversized_rpc_frames(self):
        with self.assertRaises(ProtocolError):
            recv_exact(FragmentSocket([b"ab"]), 4, Operation())
        with self.assertRaises(ProtocolError):
            recv_exact(FragmentSocket([]), 10, Operation(ScanOptions(max_response_bytes=5)))

    def test_cancel_is_local_to_operation(self):
        cancelled, active = Operation(), Operation()
        cancelled.cancel.set()
        with self.assertRaises(Cancelled):
            recv_exact(FragmentSocket([b"abc"]), 3, cancelled)
        self.assertEqual(recv_exact(FragmentSocket([b"abc"]), 3, active), b"abc")

    def test_deadline_checked_between_chunks(self):
        operation = Operation(ScanOptions(device_timeout=0.01))
        operation.started -= 1
        with self.assertRaises(DeadlineExceeded):
            recv_exact(FragmentSocket([b"abc"]), 3, operation)

    def test_real_tcp_does_not_stop_at_nested_closing_brace(self):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        failures = []
        def respond():
            try:
                with server.accept()[0] as conn:
                    conn.recv(4096)
                    conn.sendall(b'{"STATS":[{"Type":"Antminer S21"}')
                    time.sleep(0.03)
                    conn.sendall(b'],"SUMMARY":[{"Elapsed":42}]}\x00')
            except Exception as exc:
                failures.append(exc)
            finally:
                server.close()
        thread = threading.Thread(target=respond)
        thread.start()
        try:
            with Transport("127.0.0.1", Operation()) as transport:
                response = transport.cgminer("stats", port=port)
            self.assertEqual(response["SUMMARY"][0]["Elapsed"], 42)
        finally:
            thread.join(timeout=3)
        self.assertFalse(failures)


if __name__ == "__main__":
    unittest.main()
