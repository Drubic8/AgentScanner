"""Read-only RPC log download: framing, errors, budgets and cancellation."""
from contextlib import contextmanager
import gzip
import io
import json
import struct
import tarfile
from threading import Event
import unittest
from unittest.mock import patch

from miner_scanner.access import AccessProfiles
import miner_scanner.device_logs as logs
from miner_scanner.runtime import DeadlineExceeded


DEVICE = {'IP': '192.0.2.249', 'ProfileId': 'microbt.rpc3'}


def sample_archive():
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        for name, text in (('miner-state.log', 'fan 1 error\n'), ('system.log', 'kernel ready\n')):
            body = text.encode('utf-8')
            info = tarfile.TarInfo('192.0.2.249.logs/' + name)
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
    return gzip.compress(raw.getvalue(), mtime=0)


def wire(archive, **override):
    response = dict(code=0, desc='get.log.download', msg={'logsize': str(len(archive))})
    response.update(override)
    body = json.dumps(response).encode('utf-8')
    return struct.pack('<I', len(body)) + body + archive


class WireFactory:
    def __init__(self, data, *, fragment=65536, timeouts=0, cancel_on_recv=None):
        self.data = data
        self.fragment = fragment
        self.timeouts = timeouts
        self.cancel_on_recv = cancel_on_recv
        self.sent = []
        self.credentials = []
        self.ports = []
        self.closed = False
        self.operation = None

    def __call__(self, ip, operation, credentials):
        self.operation = operation
        self.credentials.append(credentials)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    @contextmanager
    def connection(self, port):
        self.ports.append(port)
        yield self

    def settimeout(self, timeout):
        self.timeout = timeout

    def sendall(self, data):
        self.sent.append(data)

    def recv(self, length):
        if self.cancel_on_recv is not None:
            self.cancel_on_recv.set()
            raise TimeoutError()
        if self.timeouts:
            self.timeouts -= 1
            raise TimeoutError()
        count = min(length, self.fragment)
        result, self.data = self.data[:count], self.data[count:]
        return result


class WhatsminerDeviceLogTests(unittest.TestCase):
    def read(self, data, kind='miner', **kwargs):
        factory = WireFactory(data, **kwargs)
        result = logs.read_device_log(DEVICE, kind, access_profiles=AccessProfiles([]),
                                     transport_factory=factory)
        return result, factory

    def test_family_support_is_independent_of_write_access_and_auth_profiles(self):
        self.assertEqual(logs.supported_log_kinds(DEVICE), ('miner', 'system'))
        factory = WireFactory(wire(sample_archive()))
        with patch.object(AccessProfiles, 'candidates', side_effect=AssertionError('No credentials needed')):
            result = logs.read_device_log(DEVICE, access_profiles=AccessProfiles([]), transport_factory=factory)
        self.assertEqual(result.status, 'succeeded')
        self.assertEqual(factory.credentials, [None])
        self.assertEqual(factory.ports, [4433])
        self.assertEqual(factory.operation.options.device_timeout, 60)
        self.assertEqual(factory.operation.options.max_response_bytes, logs.MAX_LOG_BYTES)
        packet, = factory.sent
        size = struct.unpack('<I', packet[:4])[0]
        self.assertEqual(size, len(packet[4:]))
        self.assertEqual(json.loads(packet[4:]), {'cmd':'get.log.download'})
        self.assertEqual(factory.operation.request_count, 1)
        self.assertTrue(factory.closed)

    def test_fragmented_and_coalesced_frames_select_current_log(self):
        for fragment in (1, 3, 10240, 65536):
            for kind, expected in (('miner', 'fan 1 error\n'), ('system', 'kernel ready\n')):
                with self.subTest(fragment=fragment, kind=kind):
                    result, factory = self.read(wire(sample_archive()), kind, fragment=fragment)
                    self.assertEqual(result.status, 'succeeded')
                    self.assertEqual(result.text, expected)
                    self.assertEqual(result.kind, kind)
                    self.assertFalse(result.truncated)
                    self.assertTrue(result.source.startswith('get.log.download/'))

    def test_short_socket_timeouts_allow_archive_preparation(self):
        result, factory = self.read(wire(sample_archive()), timeouts=3)
        self.assertEqual(result.status, 'succeeded')
        self.assertLessEqual(factory.timeout, 0.5)

    def test_cancel_during_wait_closes_socket_without_more_requests(self):
        cancel = Event()
        factory = WireFactory(wire(sample_archive()), cancel_on_recv=cancel)
        result = logs.read_device_log(DEVICE, cancel=cancel, transport_factory=factory)
        self.assertEqual(result.status, 'cancelled')
        self.assertEqual(result.text, '')
        self.assertEqual(len(factory.sent), 1)
        self.assertTrue(factory.closed)

    def test_deadline_does_not_retry_download(self):
        with patch.object(logs, '_receive_log_bytes', side_effect=DeadlineExceeded('budget')):
            result, factory = self.read(wire(sample_archive()))
        self.assertEqual(result.status, 'timeout')
        self.assertEqual(len(factory.sent), 1)
        self.assertTrue(factory.closed)

    def test_official_error_codes_do_not_download_or_leak_messages(self):
        for code, expected in ((-2, 'unsupported'), (-4, 'auth_required'), (-1, 'unavailable'), (-5, 'unavailable')):
            result, factory = self.read(wire(b'', code=code, msg='password=private'))
            self.assertEqual(result.status, expected)
            self.assertEqual(result.text, '')
            self.assertNotIn('private', repr(result))
            self.assertTrue(factory.closed)

    def test_response_contract_and_declared_archive_limits(self):
        for changes in ({'desc':'get.other'}, {'code':True}, {'msg':None},
                        *({'msg':{'logsize':value}} for value in (True, 0, -1, '-1', '1e6', '0'*100, ' 12', None))):
            with self.subTest(changes=changes):
                result, _ = self.read(wire(b'', **changes))
                self.assertEqual(result.status, 'invalid_response')
        result, _ = self.read(wire(b'', msg={'logsize':logs.MAX_ARCHIVE_BYTES + 1}))
        self.assertEqual(result.status, 'too_large')

    def test_truncated_and_invalid_json_or_archive_frames_are_not_success(self):
        data = wire(sample_archive())
        for payload in (data[:2], data[:15], data[:-4], struct.pack('<I', 0),
                        struct.pack('<I', 65537), struct.pack('<I', 1) + b'x', wire(b'not gzip')):
            with self.subTest(length=len(payload)):
                result, _ = self.read(payload)
                self.assertEqual(result.status, 'invalid_response')
                self.assertEqual(result.text, '')

    def test_missing_current_file_maps_to_unsupported_not_empty_success(self):
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw,mode='w',format=tarfile.USTAR_FORMAT):
            pass
        result, _ = self.read(wire(gzip.compress(raw.getvalue())))
        self.assertEqual(result.status, 'unsupported')


if __name__ == '__main__':
    unittest.main()
