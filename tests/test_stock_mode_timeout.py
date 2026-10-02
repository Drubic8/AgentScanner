"""Read-only recovery of slow stock mode APIs; power writes use fake devices."""
from copy import deepcopy
import unittest
from unittest.mock import patch, Mock
import requests

from miner_scanner.commands import execute_command
from miner_scanner.models import Credentials
from miner_scanner.profiles import ProfileRegistry
from miner_scanner.repository import DeviceRepository
from miner_scanner.runtime import AuthenticationError, Operation, ScanOptions
from miner_scanner.service import ScannerService
from miner_scanner.transports import Transport
from tests.fakes import FakeFactory, FakeTransport, stock


def device(model='Antminer T21', version='Thu Sep 26 11:39:05 CST 2024'):
    data = stock()
    data['version']['VERSION'][0] = {'Type': model, 'API': '3.1'}
    data['stats']['STATS'][0].update(Type=model, **{'GHS 5s': 0})
    data['summary']['SUMMARY'][0]['GHS 5s'] = 0
    data['system'] = {'minertype': model, 'system_filesystem_version': version}
    data['config'] = {'pools': [{'url':'stratum+tcp://example.invalid:3333', 'user':'fixture', 'pass':'x'}],
                      'bitmain-fan-ctrl': False, 'bitmain-fan-pwm': '100',
                      'bitmain-work-mode': '1', 'bitmain-hashrate-percent': '100'}
    return data


class SlowConfig(FakeTransport):
    def http_json(self, path, method='GET', **kwargs):
        if path == '/cgi-bin/get_miner_conf.cgi':
            self.factory.config_reads.append(kwargs.get('read_timeout'))
            if len(self.factory.config_reads) <= self.factory.failures:
                self.factory.calls.append((self.ip, 'config'))
                self.operation.request_count += 1
                raise self.factory.failure_type()
        return super().http_json(path, method, **kwargs)

    def http(self, path, method='GET', *, payload=None, **kwargs):
        result = super().http(path, method, payload=payload, **kwargs)
        if method == 'POST' and path.endswith('set_miner_conf.cgi'):
            self.factory.data['config']['bitmain-work-mode'] = str(payload['miner-mode'])
        return result


class StockModeTests(unittest.TestCase):
    def service(self, data=None, failures=0, failure_type=requests.ReadTimeout):
        factory = FakeFactory(data or device())
        factory.config_reads, factory.failures, factory.failure_type = [], failures, failure_type
        factory.apply_mode = False
        repository = DeviceRepository(':memory:')
        self.addCleanup(repository.close)
        service = ScannerService(repository=repository,
                                 transport_factory=lambda *args: SlowConfig(factory, *args))
        service.set_default_credentials(Credentials('root', 'root'))
        return service, factory

    def test_slow_config_recovers_sleep_once(self):
        service, factory = self.service(failures=1)
        record = service.poll('192.0.2.4')
        self.assertEqual(record.display['Status'], 'Sleep')
        self.assertEqual(record.display['Error'], '')
        self.assertEqual(factory.config_reads, [None, 4.0])
        self.assertFalse(factory.writes)

    def test_persistent_timeout_is_explicit_and_does_not_infer_sleep(self):
        service, factory = self.service(failures=99)
        record = service.poll('192.0.2.4')
        self.assertEqual(record.display['Status'], 'Unknown')
        self.assertEqual(record.display['Error'], 'CONFIG TIMEOUT')
        self.assertEqual(len(factory.config_reads), 2)
        self.assertFalse(factory.writes)

    def test_auth_failure_is_not_retried_as_a_timeout(self):
        service, factory = self.service(failures=99, failure_type=AuthenticationError)
        record = service.poll('192.0.2.4')
        self.assertEqual(record.display['Error'], 'AUTH REQUIRED')
        self.assertEqual(len(factory.config_reads), 1)

    def test_healthy_scan_adds_no_retry(self):
        service, factory = self.service()
        self.assertEqual(service.poll('192.0.2.4').display['Status'], 'Sleep')
        self.assertEqual(factory.config_reads, [None])

    def test_power_modes_match_exact_builds_and_preserve_other_settings(self):
        for model, version, action, target, wrong in (
            ('Antminer T21', 'Thu Sep 26 11:39:05 CST 2024', 'hem', 2, 'low'),
            ('Antminer S21', 'FR-1.12(251009-S21)', 'low', 3, 'hem'),
        ):
            with self.subTest(model=model):
                data = device(model, version)
                if model == 'Antminer S21':
                    data['config']['bitmain-user-ip-cat'] = '0'
                service, factory = self.service(data)
                record = service.poll('192.0.2.4')
                self.assertEqual(record.capabilities[action], 'unverified')
                self.assertEqual(record.capabilities[wrong], 'unsupported')
                self.assertEqual(execute_command(service, '192.0.2.4', action).status, 'unsupported')
                self.assertFalse(factory.writes)
                baseline = deepcopy(data['config'])
                with patch.object(Operation, 'pause', lambda *_: None):
                    result = execute_command(service, '192.0.2.4', action, allow_unverified=True)
                self.assertEqual(result.status, 'succeeded')
                payload = factory.writes[-1][2]
                self.assertEqual(payload, {**{k:v for k,v in baseline.items() if k != 'bitmain-work-mode'},
                                           'miner-mode': target})
                self.assertNotIn('freq-level', payload)
                data['system']['system_filesystem_version'] = 'unknown build'
                self.assertEqual(ProfileRegistry().resolve(data).id, 'bitmain.stock')

    def test_http_override_respects_total_budget(self):
        operation = Operation(ScanOptions(read_timeout=2, device_timeout=3))
        transport = Transport('192.0.2.4', operation)
        self.addCleanup(transport.close)
        response = Mock(status_code=200)
        response.raw.read1.return_value = b''
        context = Mock()
        context.__enter__ = Mock(return_value=response)
        context.__exit__ = Mock(return_value=False)
        with patch.object(transport.session, 'request', return_value=context) as request:
            transport.http('/cgi-bin/get_miner_conf.cgi', read_timeout=4)
        timeout = request.call_args.kwargs['timeout'][1]
        self.assertGreater(timeout, 2)
        self.assertLessEqual(timeout, 3)
