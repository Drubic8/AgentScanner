from copy import deepcopy
import unittest

from miner_scanner.drivers import make_record
from miner_scanner.access import AccessProfiles, standard_profiles
from miner_scanner.profiles import ProfileRegistry
from miner_scanner.repository import DeviceRepository
from miner_scanner.service import ScannerService
from miner_scanner.runtime import AuthenticationError
from tests.fakes import FakeFactory, FakeTransport


def status_fixture():
    """Sanitized schema observed on X16-Q; no customer pool or hardware IDs."""
    return {
        'summary': {'miner': 'JASMINER X16-Q 1950',
                    'version': 'Mon Oct 14 14:17:39 CST 2024',
                    'machine_sn': 'DEMO-X16Q-001', 'uptime': 73691,
                    'rt': '1786.59 MH/s', 'avg': '1980.56 MH/s',
                    'temp_min': 52, 'temp_max': 53},
        'boards': {'fan_num': 4, 'fan1': 1260, 'fan2': 1350,
                   'fan3': 1320, 'fan4': 0,
                   'board': [{'asic0_temp': 53, 'asic1_temp': 52, 'asic2_temp': 52}]},
        'pools': {'pool': [{'status': 'in use', 'url': 'stratum+tcp://pool.example.invalid',
                            'user': 'demo.worker'}]},
    }


class JasminerTests(unittest.TestCase):
    def record(self, status):
        data = {'jasminer_status': status}
        return make_record(ProfileRegistry().resolve(data), '192.0.2.14', data)

    def test_live_schema_retains_build_hardware_id_units_and_zero_sensor(self):
        status = status_fixture()
        original = deepcopy(status)
        r = self.record(status)
        self.assertEqual(r.identity.model, 'JASMINER X16-Q 1950')
        self.assertEqual(r.identity.firmware_version, status['summary']['version'])
        self.assertEqual(r.identity.serial, 'DEMO-X16Q-001')
        self.assertAlmostEqual(r.telemetry.rate, 1786.59e6)
        self.assertEqual(r.display['Real'], '1786.59 MH/s')
        self.assertEqual(r.telemetry.fan_rpm, [1260, 1350, 1320, 0])
        self.assertEqual(r.display['Fan'], '1260 1350 1320 0')
        self.assertEqual(r.telemetry.temperatures_c, [52, 52, 53])
        self.assertEqual(status, original)

    def test_gui_make_filter_and_warm_poll_keep_device(self):
        factory = FakeFactory({'jasminer_status': status_fixture()})
        repo = DeviceRepository(':memory:')
        self.addCleanup(repo.close)
        scanner = ScannerService(repository=repo, transport_factory=factory)
        filters = ['Bitmain', 'MicroBT', 'Elphapex', 'Canaan', 'iPollo', 'Jasminer']
        first = scanner.poll('192.0.2.14', filters)
        self.assertIsNotNone(first)
        factory.calls.clear()
        second = scanner.poll('192.0.2.14', filters)
        self.assertEqual(second.identity.device_id, first.identity.device_id)
        self.assertEqual(factory.calls, [('192.0.2.14', 'jasminer_status')])

    def test_list_blocks_and_malformed_boards_are_tolerated(self):
        status = status_fixture()
        status['summary'] = [None, status['summary']]
        status['boards'] = [status['boards']]
        status['boards'][0]['board'].insert(0, None)
        r = self.record(status)
        self.assertEqual(r.telemetry.temperatures_c, [52, 52, 53])
        self.assertEqual(r.identity.serial, 'DEMO-X16Q-001')
        self.assertEqual(r.display['Error'], '')

    def test_fan_count_limits_channels_without_dropping_zero(self):
        status = status_fixture()
        status['boards'].update(fan_num=2, fan1=0, fan2=900, fan3=3000)
        self.assertEqual(self.record(status).telemetry.fan_rpm, [0, 900])

    def test_missing_sensors_and_unit_stay_unknown(self):
        status = status_fixture()
        status['boards'] = []
        for field in ('rt', 'avg', 'uptime', 'temp_min', 'temp_max'):
            status['summary'].pop(field)
        r = self.record(status)
        self.assertIsNone(r.telemetry.rate)
        self.assertIsNone(r.telemetry.uptime_seconds)
        self.assertEqual(r.telemetry.fan_rpm, [])
        self.assertEqual(r.display['Temp'], '—')
        self.assertEqual(r.display['Error'], '')

    def test_hashrate_units_are_explicit_not_inferred_from_magnitude(self):
        for value, expected in [('2 GH/s', 2e9), ('0 MH/s', 0), ('2000', None)]:
            with self.subTest(value=value):
                status = status_fixture()
                status['summary']['rt'] = value
                self.assertEqual(self.record(status).telemetry.rate, expected)

    def test_pool_name_cannot_identify_jasminer(self):
        status = status_fixture()
        status['summary']['miner'] = 'Unknown device'
        status['pools']['pool'][0]['user'] = 'Jasminer'
        self.assertIsNone(ProfileRegistry().resolve({'jasminer_status': status}))

    def test_temperature_summary_fallback(self):
        status = status_fixture()
        status['boards']['board'] = []
        self.assertEqual(self.record(status).telemetry.temperatures_c, [52, 53])

    def test_authenticated_repeat_scan_and_restart_keep_discovery_credentials(self):
        class AuthTransport(FakeTransport):
            def __init__(self, factory, ip, operation, credentials=None):
                super().__init__(factory, ip, operation, credentials)
                self.credentials = credentials

            def http_json(self, path, method='GET', **kwargs):
                if path == '/cgi-bin/minerStatus.cgi' and (
                        self.credentials is None or self.credentials.username != 'root'
                        or self.credentials.password != 'root'):
                    raise AuthenticationError('Required')
                return super().http_json(path, method, **kwargs)

        class AuthFactory(FakeFactory):
            def __call__(self, ip, operation, credentials=None):
                return AuthTransport(self, ip, operation, credentials)

        factory = AuthFactory({'jasminer_status': status_fixture()})
        repo = DeviceRepository(':memory:')
        self.addCleanup(repo.close)
        def service():
            scanner = ScannerService(repository=repo, transport_factory=factory)
            scanner.set_access_profiles(AccessProfiles(standard_profiles()))
            return scanner
        scanner = service()
        first = scanner.poll('192.0.2.14')
        self.assertFalse(first.telemetry.stale)
        for restarted in (False, True):
            if restarted:
                scanner = service()
            record = scanner.poll('192.0.2.14')
            self.assertFalse(record.telemetry.stale)
            self.assertEqual(record.identity.device_id, first.identity.device_id)
            self.assertEqual(record.display['Error'], '')
        self.assertFalse(factory.writes)


if __name__ == '__main__':
    unittest.main()
