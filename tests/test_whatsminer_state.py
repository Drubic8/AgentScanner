import unittest
import json
from pathlib import Path

from miner_scanner.parsers.whatsminer import parse_whatsminer_data
from tests.test_whatsminer_commands import info


class WhatsminerStateTests(unittest.TestCase):
    def test_observed_m61s_reply_survives_full_scanner_normalization(self):
        from miner_scanner.service import ScannerService
        from miner_scanner.repository import DeviceRepository
        from tests.fakes import FakeFactory
        data = json.loads((Path(__file__).parent / 'fixtures/whatsminer_m61s_rpc3_sleep.json').read_text())
        repository = DeviceRepository(':memory:')
        self.addCleanup(repository.close)
        factory = FakeFactory(data)
        service = ScannerService(repository=repository, transport_factory=factory)
        for _ in range(2):
            record = service.poll('192.0.2.249')
            self.assertIsNotNone(record)
            self.assertFalse(record.telemetry.stale)
            self.assertEqual(record.to_legacy()['Status'], 'Sleep')
            self.assertEqual(record.telemetry.mining_state, 'stopped')
            self.assertEqual(record.telemetry.rate, 0)
        self.assertFalse(factory.writes)

    def test_stopped_rpc_service_does_not_require_summary_uptime(self):
        for working in (False, 'false'):
            response = info()
            response['msg']['miner']['working'] = working
            for summary in (None, {'msg': {'elapsed': 0}}, {'msg': {'elapsed': 30}}):
                row = parse_whatsminer_data('192.0.2.1', response, summary, None)
                self.assertEqual(row['Status'], 'Sleep')

    def test_residual_hash_reports_stopping(self):
        response = info()
        response['msg']['miner']['working'] = False
        row = parse_whatsminer_data('192.0.2.1', response, {'msg': {'hash-realtime': 1}}, None)
        self.assertEqual(row['Status'], 'Stopping')

    def test_zero_hash_alone_and_unknown_protocol_do_not_mean_sleep(self):
        self.assertEqual(parse_whatsminer_data('192.0.2.1', {}, None, None)['Status'], 'WaitWork')
        response = info()
        response['msg']['miner']['working'] = False
        response['msg']['system']['api'] = '4.0.0'
        self.assertEqual(parse_whatsminer_data('192.0.2.1', response, None, None)['Status'], 'WaitWork')

    def test_hardware_error_is_preserved(self):
        response = info()
        response['msg']['miner']['working'] = False
        response['msg']['error-code'] = [2310]
        row = parse_whatsminer_data('192.0.2.1', response, None, None)
        self.assertEqual(row['Status'], 'Error')
        self.assertEqual(row['Error'], '2310')
