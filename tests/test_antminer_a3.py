"""A3 telemetry regressions using sanitized hardware evidence and no LAN access."""
from contextlib import nullcontext
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from miner_scanner.drivers import make_record
from miner_scanner.profiles import ProfileRegistry
from miner_scanner.protocol_compat import repair_antminer_stats
from miner_scanner.repository import DeviceRepository
from miner_scanner.runtime import Operation, ProtocolError
from miner_scanner.service import ScannerService
from miner_scanner.transports import Transport
from tests.fakes import FakeFactory
from tests.test_transports import FragmentSocket


class AntminerA3Tests(unittest.TestCase):
    def setUp(self):
        fixture = Path(__file__).parent / 'fixtures' / 'antminer_a3_stock_hardware.json'
        self.capture = json.loads(fixture.read_text(encoding='utf-8'))
        self.data = deepcopy(self.capture['data'])
        self.data['stats'] = json.loads(repair_antminer_stats(self.capture['stats_raw']))

    def record(self, data=None):
        data = self.data if data is None else data
        return make_record(ProfileRegistry().resolve(data), '192.0.2.3', data)

    def test_hardware_capture_identity_rates_and_sensors(self):
        record = self.record()
        self.assertEqual(record.identity.profile_id, 'bitmain.stock')
        self.assertEqual(record.identity.make, 'Bitmain')
        self.assertEqual(record.identity.model, 'Antminer A3')
        self.assertEqual(record.identity.firmware_version, 'Fri Dec 29 17:12:57 CST 2017')
        self.assertEqual(record.identity.api_version, '3.1')
        self.assertEqual(record.display['Algo'], 'Blake2B')
        self.assertEqual(record.display['Real'], '608.80 GH/s')
        self.assertEqual(record.display['Avg'], '603.92 GH/s')
        self.assertEqual(record.telemetry.rate, 608.8e9)
        self.assertEqual(record.telemetry.average_rate, 603.92e9)
        self.assertEqual(record.display['Fan'], '5130 5190')
        self.assertEqual(record.telemetry.fan_rpm, [5130, 5190])
        self.assertEqual(record.display['Temp'], '82 80')
        self.assertEqual(record.telemetry.temperatures_c, [82, 80])
        self.assertEqual(record.display['Status'], 'Running')
        self.assertEqual(record.display['Error'], '')

    def test_summary_loss_uses_stats_with_same_units(self):
        del self.data['summary']
        record = self.record()
        self.assertEqual(record.display['Real'], '604.76 GH/s')
        self.assertEqual(record.display['Avg'], '603.79 GH/s')

    def test_zero_rate_and_failed_fan_are_not_missing(self):
        self.data['summary']['SUMMARY'][0].update({'GHS 5s': 0, 'GHS av': 0})
        self.data['stats']['STATS'][1]['fan1'] = 0
        record = self.record()
        self.assertEqual(record.display['Real'], '0.00 GH/s')
        self.assertEqual(record.display['Avg'], '0.00 GH/s')
        self.assertEqual(record.display['Fan'], '0 5190')
        self.assertEqual(record.telemetry.fan_rpm, [0, 5190])
        # No explicit sleep flag was read: a zero rate alone does not prove sleep.
        self.assertEqual(record.display['Status'], 'Unknown')

    def test_bare_system_model_is_identified_without_cgminer(self):
        record = self.record({'system': {'minertype': 'A3'}})
        self.assertEqual(record.identity.make, 'Bitmain')
        self.assertEqual(record.display['Model'], 'Antminer A3')
        self.assertEqual(record.display['Algo'], 'Blake2B')
        self.assertIsNone(record.telemetry.rate)

    def test_other_a_models_do_not_inherit_a3_algorithm(self):
        for model in ('A30', 'A3 Pro', 'A4'):
            with self.subTest(model=model):
                record = self.record({'version': {'VERSION': [{'Type': 'Antminer ' + model}]}})
                self.assertEqual(record.display['Algo'], 'Unknown')

    def test_fragmented_tcp_reply_is_repaired_only_after_completion(self):
        raw = self.capture['stats_raw'].encode('utf-8')
        fragments = [raw[:100], raw[100:300], raw[300:] + b'\x00']
        with Transport('192.0.2.3', Operation()) as transport:
            with patch.object(transport, 'connection', return_value=nullcontext(FragmentSocket(fragments))):
                self.assertEqual(transport.cgminer('stats', repair=repair_antminer_stats), self.data['stats'])
            self.assertIn('cgminer:legacy_json_repaired', transport.operation.errors)
            with patch.object(transport, 'connection', return_value=nullcontext(FragmentSocket(fragments))):
                with self.assertRaises(ProtocolError):
                    transport.cgminer('stats')

    def test_repair_rejects_truncated_or_other_device_reply(self):
        raw = self.capture['stats_raw']
        for invalid in (raw[:-20], raw.replace('Antminer A3', 'Antminer A30'),
                        raw.replace('Antminer A3', 'Other A3')):
            with self.subTest(invalid=invalid[:80]), self.assertRaises(ValueError):
                repair_antminer_stats(invalid)

    def test_service_discovery_and_cached_poll_keep_a3_telemetry(self):
        factory = FakeFactory(self.data)
        repository = DeviceRepository(':memory:')
        self.addCleanup(repository.close)
        service = ScannerService(repository=repository, transport_factory=factory)
        for _ in range(2):
            record = service.poll('192.0.2.3')
            self.assertEqual(record.display['Model'], 'Antminer A3')
            self.assertEqual(record.display['Real'], '608.80 GH/s')
            self.assertEqual(record.display['Temp'], '82 80')
            self.assertFalse(record.telemetry.stale)
        self.assertFalse(factory.writes)


if __name__ == '__main__':
    unittest.main()
