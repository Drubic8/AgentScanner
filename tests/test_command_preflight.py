"""Bulk compatibility checks are read-only and retain the selected identity."""
from copy import deepcopy
from dataclasses import replace
from threading import Event
import unittest
from unittest.mock import patch

from miner_scanner.models import CommandResult, Credentials
from miner_scanner.preflight import preflight_batch, is_retry_safe
from miner_scanner.profiles import ProfileRegistry
from miner_scanner.repository import DeviceRepository
from miner_scanner.service import ScannerService
from tests.fakes import FakeFactory


class CommandPreflightTests(unittest.TestCase):
    def setUp(self):
        self.factory = FakeFactory()
        self.repository = DeviceRepository(':memory:')
        self.addCleanup(self.repository.close)
        catalog = ProfileRegistry()
        profile = replace(catalog.by_id['bitmain.stock'],
            match={'models': ['Antminer S21'], 'firmware_versions': ['test-1']},
            verified_commands=('identify_on', 'identify_off', 'reboot'))
        self.service = ScannerService(repository=self.repository, transport_factory=self.factory,
            registry=ProfileRegistry([profile, catalog.by_id['canaan.avalon'], catalog.by_id['microbt.rpc3']]))
        self.service.set_default_credentials(Credentials('root', 'root'))

    def targets(self, count=1):
        return [self.service.poll(f'192.0.2.{index + 1}').to_legacy() for index in range(count)]

    def run_preflight(self, rows, **kwargs):
        return list(preflight_batch(self.service, rows, 'led_on', **kwargs))

    def test_force_reads_deduplicates_preserves_targets_and_never_writes_commands(self):
        row = self.targets()[0]
        with patch.object(self.service, 'poll', wraps=self.service.poll) as poll:
            results = self.run_preflight([row, deepcopy(row)])
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].ready)
        self.assertIsNot(results[0].target, row)
        self.assertIsNot(results[0].target['Identity'], row['Identity'])
        self.assertEqual(results[0].target['Identity']['fingerprint'], row['Identity']['fingerprint'])
        self.assertEqual(results[0].record['DeviceId'], row['DeviceId'])
        self.assertTrue(poll.call_args.kwargs['force_identify'])
        self.assertFalse(self.factory.writes)
        self.assertEqual(self.repository._db().execute('SELECT count(*) FROM commands').fetchone()[0], 0)

    def test_mixed_readiness_and_explicit_block_cannot_be_bypassed(self):
        rows = self.targets(5)
        records = {row['IP']: deepcopy(self.service.get_record(row['IP'])) for row in rows}
        records[rows[1]['IP']] = None
        denied = records[rows[2]['IP']]
        denied.telemetry.stale = True
        denied.display['Error'] = 'AUTH REQUIRED'
        records[rows[3]['IP']].identity.fingerprint = 'changed-after-selection'
        self.service._control_contracts[rows[4]['IP']] = {
            'blocked': {'identify_on'}, 'rules': {}, 'reason': 'Known incompatible interface'}
        with patch.object(self.service, 'poll', side_effect=lambda ip, **_: records[ip]):
            results = {item.ip: item for item in self.run_preflight(rows,
                experimental_ids=[rows[4]['DeviceId']])}
        self.assertEqual([results[row['IP']].status for row in rows],
                         ['ready', 'offline', 'auth_required', 'changed', 'unsupported'])
        self.assertEqual(results[rows[4]['IP']].message, 'Known incompatible interface')
        self.assertFalse(self.factory.writes)

    def test_selected_fingerprint_is_checked_even_after_cache_was_refreshed(self):
        row = self.targets()[0]
        changed = deepcopy(self.service.get_record(row['IP']))
        changed.identity.fingerprint = 'new-firmware'
        changed.identity.firmware_version = 'new-build'
        self.service._records[row['IP']] = changed
        with patch.object(self.service, 'poll', return_value=changed):
            result = self.run_preflight([row])[0]
        self.assertEqual(result.status, 'changed')
        self.assertEqual(result.target['Identity']['fingerprint'], row['Identity']['fingerprint'])

    def test_experimental_admission_does_not_claim_confirmed_api_compatibility(self):
        row = self.targets()[0]
        current = deepcopy(self.service.get_record(row['IP']))
        profile_id = current.identity.profile_id
        self.service.registry.by_id[profile_id] = replace(self.service.registry.by_id[profile_id],
                                                         verified_commands=())
        self.service._control_contracts[row['IP']] = {}
        with patch.object(self.service, 'poll', return_value=current):
            self.assertEqual(self.run_preflight([row])[0].status, 'unsupported')
            admitted = self.run_preflight([row], experimental_ids=[row['DeviceId']])[0]
            self.assertTrue(admitted.ready)
            self.assertEqual(admitted.message,
                'Экспериментальное управление разрешено для выбранного ASIC; совместимость команды не подтверждена.')
            self.service._control_contracts[row['IP']] = {'blocked': {'identify_on'}, 'reason': 'Blocked contract'}
            blocked = self.run_preflight([row], experimental_ids=[row['DeviceId']])[0]
            self.assertEqual(blocked.status, 'unsupported')
            self.assertEqual(blocked.message, 'Blocked contract')

    def test_missing_required_credentials_are_explained_but_avalon_needs_no_account(self):
        row = self.targets()[0]
        current = deepcopy(self.service.get_record(row['IP']))
        self.service.set_default_credentials(None)
        with patch.object(self.service, 'poll', return_value=current):
            self.assertEqual(self.run_preflight([row])[0].status, 'auth_required')
        current.identity.profile_id = 'canaan.avalon'
        self.service._records[row['IP']] = current
        self.service._control_contracts[row['IP']] = {'rules': {'identify_on': None}, 'blocked': set()}
        row = current.to_legacy()
        with patch.object(self.service, 'poll', return_value=current):
            self.assertTrue(self.run_preflight([row])[0].ready)

    def test_whatsminer_rpc_credentials_do_not_apply_to_declarative_legacy_contract(self):
        row = self.targets()[0]
        current = deepcopy(self.service.get_record(row['IP']))
        current.identity.profile_id = 'microbt.rpc3'
        self.service._records[row['IP']] = current
        self.service.set_default_credentials(None)
        self.service._control_contracts[row['IP']] = {'rules': {'identify_on': None}, 'blocked': set()}
        row = current.to_legacy()
        with patch.object(self.service, 'poll', return_value=current):
            self.assertEqual(self.run_preflight([row])[0].status, 'auth_required')
            self.service.set_default_credentials(Credentials('super', 'synthetic-api-password'))
            self.service._control_contracts[row['IP']] = {'rules': {'identify_on': None}, 'blocked': set()}
            self.assertTrue(self.run_preflight([row])[0].ready)
            self.service.set_default_credentials(None)
            self.service._control_contracts[row['IP']] = {'rules': {'identify_on': {
                'path': '/legacy-led', 'verify': {'path': '/legacy-status', 'field': 'led', 'equals': True}}},
                'blocked': set()}
            self.assertTrue(self.run_preflight([row])[0].ready)

    def test_a_failed_check_does_not_abort_others_or_expose_exception_secrets(self):
        rows = self.targets(2)
        current = self.service.get_record(rows[1]['IP'])

        def poll(ip, **_):
            if ip == rows[0]['IP']:
                raise RuntimeError('password=must-not-appear-in-the-result')
            return current

        with patch.object(self.service, 'poll', side_effect=poll):
            results = {item.ip: item for item in self.run_preflight(rows)}
        self.assertEqual(len(results), 2)
        self.assertEqual(results[rows[0]['IP']].status, 'failed')
        self.assertIn('RuntimeError', results[rows[0]['IP']].message)
        self.assertNotIn('must-not-appear', results[rows[0]['IP']].message)
        self.assertTrue(results[rows[1]['IP']].ready)

    def test_cancellation_yields_each_target_without_further_reads(self):
        rows = self.targets(8)
        cancel = Event()

        def poll(ip, **_):
            cancel.set()
            return self.service.get_record(ip)

        with patch.object(self.service, 'poll', side_effect=poll) as mocked:
            results = self.run_preflight([*rows, rows[0]], workers=1, cancel=cancel)
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(len(results), 8)
        self.assertTrue(all(item.status == 'cancelled' for item in results))
        self.assertFalse(self.factory.writes)

    def test_invalid_worker_limit_is_rejected(self):
        for workers in (0, 33, True, '32'):
            with self.subTest(workers=workers), self.assertRaises(ValueError):
                self.run_preflight([], workers=workers)

    def test_retry_requires_explicit_no_write_outcome(self):
        for status, message, accepted, expected in (
            ('cancelled', 'stopped before write', False, True),
            ('skipped', 'identity changed', False, True),
            ('failed', 'API отклонил команду', False, True),
            ('failed', 'generic protocol failure after intent', False, False),
            ('failed', 'API отклонил команду', True, False),
            ('unconfirmed', 'no response', False, False),
            ('succeeded', 'confirmed', True, False),
            ('unsupported', 'not supported', False, False),
        ):
            with self.subTest(status=status, accepted=accepted):
                self.assertEqual(is_retry_safe(CommandResult('test', status, message, accepted)), expected)
