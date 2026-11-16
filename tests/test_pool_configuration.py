"""Pool edits use synthetic per-device state, never a real ASIC or network."""
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from threading import Event, Lock, RLock
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import requests

from miner_scanner.drivers import make_record
from miner_scanner.models import CommandResult
from miner_scanner.pool_configuration import (PoolEdit, WorkerNaming, execute_pool_batch,
                                               prepare_pool_batch, collect_pool_evidence_batch)
from miner_scanner import pool_backends
from miner_scanner.profiles import ProfileRegistry
from miner_scanner.repository import DeviceRepository
from miner_scanner.runtime import ScanOptions
from tests.fakes import stock


def config():
    return SimpleNamespace(backend_id='synthetic.config.v1', slots=[
        {'url': 'stratum+tcp://one.example.invalid:3333', 'user': 'old.one', 'pass': 'private-one'},
        {'url': 'stratum+tcp://two.example.invalid:4444', 'user': 'other.two', 'pass': 'private-two'},
        {'url': '', 'user': '', 'pass': ''}], opaque={'mode': 1, 'fan': 75})


def signature(state):
    return sha256(json.dumps([state.backend_id, state.slots, state.opaque], sort_keys=True).encode()).hexdigest()


class PoolService:
    def __init__(self, ips):
        profile = ProfileRegistry().by_id['bitmain.stock']
        self.records = {ip: make_record(profile, ip, stock()) for ip in ips}
        self.states = {ip: config() for ip in ips}
        self.repository = DeviceRepository(':memory:')
        self.options = ScanOptions()
        self.locks, self.guards = {ip: RLock() for ip in ips}, {ip: Lock() for ip in ips}
        self.writes = []
        self.read_failures = {}
        self.write_error = None
        self.apply = True
        self.accepted = True
        self.after_write = None
        self.polls = 0

    def get_record(self, ip):
        return self.records.get(ip)

    def poll(self, ip, **kwargs):
        self.polls += 1
        return self.records.get(ip)

    def credentials_for(self, ip):
        return None

    def transport_factory(self, ip, op, credentials):
        return nullcontext(SimpleNamespace(ip=ip, operation=op))

    def lock_for(self, ip):
        return self.locks[ip]

    def command_lock_for(self, ip):
        return self.guards[ip]

    def read(self, service, record, transport):
        if transport.ip in self.read_failures:
            raise self.read_failures[transport.ip]
        return deepcopy(self.states[transport.ip])

    def write(self, service, record, transport, before, slots, *, explicit_password_slots=()):
        self.writes.append((transport.ip, deepcopy(slots)))
        if self.apply:
            self.states[transport.ip].slots = deepcopy(slots)
        if self.after_write:
            self.after_write()
        if self.write_error:
            raise self.write_error
        return self.accepted


class PoolConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.service = PoolService(['192.0.2.10', '192.0.2.2', '198.51.100.10'])
        self.addCleanup(self.service.repository.close)
        for target, replacement in (
            ('read_state', self.service.read), ('write_state', self.service.write),
            ('validate_state', lambda state, slots, **kwargs: None),
            ('state_signature', signature),
            ('equivalent_state', lambda a, b, **kw: a.backend_id == b.backend_id and a.opaque == b.opaque)):
            patcher = patch.object(pool_backends, target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch('miner_scanner.pool_configuration.Operation.pause', return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def targets(self, *ips):
        return [self.service.records[ip].to_legacy() for ip in (ips or self.service.records)]

    def prepare(self, edits=None, targets=None, **kwargs):
        return list(prepare_pool_batch(self.service, targets or self.targets('192.0.2.10'),
                    edits or [PoolEdit(0, worker=WorkerNaming('TEST'))], **kwargs))

    def execute(self, plans, **kwargs):
        return dict(execute_pool_batch(self.service, plans, **kwargs))

    def test_common_name_before_dot_and_ip_index_formats(self):
        self.assertEqual(WorkerNaming('TEST', 'ip').render('192.0.2.10', 7), 'TEST.192_0_2_10')
        self.assertEqual(WorkerNaming('TEST', 'last_octet').render('192.0.2.10', 7), 'TEST.10')
        self.assertEqual(WorkerNaming('TEST', 'index', 3, 3).render('192.0.2.10', 1), 'TEST.004')
        for name in ('', 'TEST.sub', ' TEST', 'TEST\n', 'TEST\x00'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                WorkerNaming(name)

    def test_numbering_uses_numeric_ip_order_before_preflight_and_does_not_shift_on_failure(self):
        self.service.read_failures['192.0.2.2'] = OSError('private failure')
        plans = self.prepare(targets=self.targets('192.0.2.10', '192.0.2.2', '198.51.100.10'),
                             edits=[PoolEdit(0, worker=WorkerNaming('TEST', width=3))])
        by_ip = {p.ip: p for p in plans}
        self.assertEqual(by_ip['192.0.2.2'].status, 'offline')
        self.assertEqual(by_ip['192.0.2.2'].changes[0].after, 'TEST.001')
        self.assertEqual(by_ip['192.0.2.10'].changes[0].after, 'TEST.002')
        self.assertEqual(by_ip['198.51.100.10'].changes[0].after, 'TEST.003')
        self.assertNotIn('private failure', repr(plans))
        self.assertFalse(self.service.writes)

    def test_last_octet_collision_rejected_before_any_device_io(self):
        with self.assertRaisesRegex(ValueError, 'Совпадающие'):
            self.prepare(targets=self.targets('192.0.2.10', '198.51.100.10'),
                         edits=[PoolEdit(0, worker=WorkerNaming('TEST', 'last_octet'))])
        self.assertEqual(self.service.polls, 0)

    def test_preview_is_read_only_and_excludes_secret_values(self):
        plans = self.prepare(edits=[PoolEdit(0, password='new-private-value')])
        self.assertEqual(plans[0].status, 'ready')
        self.assertEqual(plans[0].changes[0].before, '<hidden>')
        self.assertEqual(plans[0].changes[0].after, '<changed>')
        self.assertFalse(self.service.writes)
        self.assertNotIn('private', repr(plans))
        self.assertNotIn('new-private-value', repr(PoolEdit(0, password='new-private-value')))

    def test_worker_change_preserves_other_slots_passwords_power_and_fan(self):
        before = deepcopy(self.service.states['192.0.2.10'])
        plans = self.prepare()
        result = self.execute(plans)['192.0.2.10']
        self.assertEqual(result.status, 'succeeded')
        self.assertTrue(result.accepted_by_api)
        after = self.service.states['192.0.2.10']
        self.assertEqual(after.slots[0]['user'], 'TEST.1')
        self.assertEqual(after.slots[0]['pass'], before.slots[0]['pass'])
        self.assertEqual(after.slots[1:], before.slots[1:])
        self.assertEqual(after.opaque, before.opaque)
        self.assertEqual(len(self.service.writes), 1)
        for (text,) in self.service.repository._db().execute('SELECT result FROM commands'):
            self.assertNotIn('private', text)
            self.assertNotIn('TEST.1', text)

    def test_unavailable_password_in_untouched_slot_blocks_whole_list_write(self):
        for password in (None, '***', '••••'):
            with self.subTest(password=password):
                item = self.service.states['192.0.2.10'].slots[1]
                if password is None:
                    item.pop('pass', None)
                else:
                    item['pass'] = password
                plan = self.prepare()[0]
                self.assertEqual(plan.status, 'unsupported')
        self.assertFalse(self.service.writes)

    def test_explicit_password_replacement_unblocks_unknown_field(self):
        self.service.states['192.0.2.10'].slots[1].pop('pass')
        plans = self.prepare(edits=[PoolEdit(0, worker=WorkerNaming('TEST')), PoolEdit(1, password='replacement')])
        self.assertEqual(plans[0].status, 'ready')
        self.assertEqual(self.execute(plans)['192.0.2.10'].status, 'succeeded')
        self.assertEqual(self.service.states['192.0.2.10'].slots[1]['pass'], 'replacement')

    def test_unchanged_values_do_not_write(self):
        self.service.states['192.0.2.10'].slots[0]['user'] = 'TEST.1'
        plans = self.prepare()
        self.assertEqual(plans[0].status, 'unchanged')
        self.assertEqual(self.execute(plans)['192.0.2.10'].status, 'skipped')
        self.assertFalse(self.service.writes)

    def test_configuration_changed_after_preview_is_not_overwritten(self):
        plans = self.prepare()
        self.service.states['192.0.2.10'].opaque['fan'] = 90
        result = self.execute(plans)['192.0.2.10']
        self.assertEqual(result.status, 'skipped')
        self.assertFalse(self.service.writes)

    def test_device_identity_changed_after_preview_is_rejected(self):
        plans = self.prepare()
        self.service.records['192.0.2.10'].identity.fingerprint = 'different-device'
        self.assertEqual(self.execute(plans)['192.0.2.10'].status, 'skipped')
        self.assertFalse(self.service.writes)

    def test_timeout_after_applied_write_is_read_back_without_repeat(self):
        plans = self.prepare()
        self.service.write_error = requests.ReadTimeout('secret-url')
        result = self.execute(plans)['192.0.2.10']
        self.assertEqual(result.status, 'succeeded')
        self.assertFalse(result.accepted_by_api)
        self.assertEqual(len(self.service.writes), 1)

    def test_timeout_without_confirmation_stays_uncertain_and_duplicate_plan_cannot_replay(self):
        plans = self.prepare()
        self.service.write_error = requests.ReadTimeout('secret')
        self.service.apply = False
        first = self.execute(plans)['192.0.2.10']
        self.assertEqual(first.status, 'unconfirmed')
        self.assertEqual(self.execute(plans)['192.0.2.10'], first)
        self.assertEqual(len(self.service.writes), 1)

    def runtime_password_api(self):
        """A runtime API never echoes the submitted password in its readback."""
        for state in self.service.states.values():
            state.backend_id = 'whatsminer.rpc3.pools'
            state.opaque['verification_kind'] = 'runtime_without_passwords'
            for slot in state.slots:
                slot.pop('pass', None)
        original_read = self.service.read

        def read(service, record, transport):
            state = original_read(service, record, transport)
            for slot in state.slots:
                slot.pop('pass', None)
            return state

        replacement = patch.object(pool_backends, 'read_state', read)
        replacement.start()
        self.addCleanup(replacement.stop)

    def test_runtime_api_demands_explicit_passwords_instead_of_guessing_defaults(self):
        self.runtime_password_api()
        plan = self.prepare()[0]
        self.assertEqual(plan.status, 'unsupported')
        self.assertIn('задайте пароль явно', plan.message)
        self.assertIsNone(plan._prepared)
        self.assertFalse(self.service.writes)
        self.assertIsNone(self.service.repository.command_result(plan.command_id))

    def test_runtime_url_worker_confirmation_does_not_claim_password_persistence(self):
        self.runtime_password_api()
        edits = [PoolEdit(0, worker=WorkerNaming('TEST'), password='  pool:пароль,1  '),
                 PoolEdit(1, password='***'), PoolEdit(2, password='')]
        plans = self.prepare(edits=edits)
        self.assertEqual(plans[0].status, 'ready', plans[0].message)
        result = self.execute(plans)['192.0.2.10']
        self.assertEqual(result.status, 'unconfirmed')
        self.assertTrue(result.accepted_by_api)
        self.assertIn('API не подтверждает сохранение паролей', result.message)
        self.assertEqual([slot['pass'] for slot in self.service.writes[0][1]],
                         ['  pool:пароль,1  ', '***', ''])
        self.assertEqual(self.service.records['192.0.2.10'].display['Worker'], 'TEST.1')
        self.assertEqual(self.execute(plans)['192.0.2.10'], result)
        self.assertEqual(len(self.service.writes), 1)
        self.assertNotIn('pool:пароль', repr(plans) + repr(result))

    def test_runtime_password_only_change_remains_unconfirmed_after_acceptance(self):
        self.runtime_password_api()
        plans = self.prepare(edits=[PoolEdit(i, password='new-secret') for i in range(3)])
        self.assertEqual(plans[0].status, 'ready')
        result = self.execute(plans)['192.0.2.10']
        self.assertEqual(result.status, 'unconfirmed')
        self.assertTrue(result.accepted_by_api)
        self.assertEqual(len(self.service.writes), 1)

    def test_runtime_readback_after_timeout_never_promotes_to_confirmed_success(self):
        self.runtime_password_api()
        self.service.write_error = requests.ReadTimeout('private-token')
        plans = self.prepare(edits=[PoolEdit(i, worker=WorkerNaming('TEST'), password='new-secret')
                                    for i in range(3)])
        result = self.execute(plans)['192.0.2.10']
        self.assertEqual(result.status, 'unconfirmed')
        self.assertFalse(result.accepted_by_api)
        self.assertEqual(len(self.service.writes), 1)

    def test_cancelled_duplicate_waiter_cannot_overwrite_another_writers_intent(self):
        plan = self.prepare()[0]
        intent = CommandResult(plan.command_id, 'unconfirmed', 'Write in progress', False,
                               plan.device_id, plan.profile_id)
        self.service.repository.journal(intent)
        guard = self.service.guards[plan.ip]
        guard.acquire()
        cancel = Event()
        cancel.set()
        try:
            result = self.execute([plan], cancel=cancel)[plan.ip]
        finally:
            guard.release()
        self.assertEqual(result.status, 'cancelled')
        self.assertEqual(self.service.repository.command_result(plan.command_id), intent)
        self.assertFalse(self.service.writes)

    def test_acceptance_without_config_change_is_not_success(self):
        self.service.apply = False
        result = self.execute(self.prepare())['192.0.2.10']
        self.assertEqual(result.status, 'unconfirmed')
        self.assertTrue(result.accepted_by_api)

    def test_explicit_rejection_is_not_success(self):
        self.service.apply = False
        self.service.accepted = False
        self.assertEqual(self.execute(self.prepare())['192.0.2.10'].status, 'failed')
        self.assertEqual(len(self.service.writes), 1)

    def test_cancel_before_write_and_after_write_have_distinct_results(self):
        cancel = Event()
        plans = self.prepare()
        cancel.set()
        self.assertEqual(self.execute(plans, cancel=cancel)['192.0.2.10'].status, 'cancelled')
        self.assertFalse(self.service.writes)
        plans = self.prepare()
        cancel.clear()
        self.service.after_write = cancel.set
        self.assertEqual(self.execute(plans, cancel=cancel)['192.0.2.10'].status, 'unconfirmed')
        self.assertEqual(len(self.service.writes), 1)
        self.assertTrue(self.service.guards['192.0.2.10'].acquire(False))
        self.service.guards['192.0.2.10'].release()

    def test_failure_on_one_device_does_not_abort_other_devices(self):
        self.service.read_failures['192.0.2.2'] = pool_backends.PoolBackendError('No contract')
        plans = self.prepare(targets=self.targets('192.0.2.2', '192.0.2.10'))
        result = self.execute(plans)
        self.assertEqual(result['192.0.2.10'].status, 'succeeded')
        self.assertEqual(result['192.0.2.2'].status, 'skipped')

    def test_preview_and_parameters_mutation_cannot_change_an_approved_write(self):
        plans = self.prepare()
        plans[0]._prepared.state.slots[0]['user'] = 'malicious-change'
        self.assertEqual(self.execute(plans)['192.0.2.10'].status, 'skipped')
        self.assertFalse(self.service.writes)

    def test_journal_failure_before_intent_prevents_write_and_other_devices_continue(self):
        plans = self.prepare(targets=self.targets('192.0.2.2', '192.0.2.10'))
        with patch.object(self.service.repository, 'journal', side_effect=OSError('disk error')):
            results = self.execute(plans)
        self.assertEqual({r.status for r in results.values()}, {'failed'})
        self.assertFalse(self.service.writes)

    def test_pool_url_validation_rejects_embedded_credentials_and_control_characters(self):
        for url in ('stratum+tcp://login:password@pool.invalid:3333', 'pool.invalid\n',
                    'https://pool.invalid', 'stratum+tcp://pool.invalid:99999'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                PoolEdit(0, url=url)

    def test_firmware_worker_auto_suffix_blocks_worker_only_not_other_changes(self):
        self.service.states['192.0.2.10'].opaque['worker_auto_suffix'] = True
        self.assertEqual(self.prepare()[0].status, 'unsupported')
        plans = self.prepare(edits=[PoolEdit(0, url='stratum+tcp://new.example.invalid:3333')])
        self.assertEqual(plans[0].status, 'ready')

    def test_explicit_password_clear_and_slot_position_are_preserved(self):
        plans = self.prepare(edits=[PoolEdit(1, password='')])
        self.assertEqual(self.execute(plans)['192.0.2.10'].status, 'succeeded')
        self.assertEqual(self.service.states['192.0.2.10'].slots[1]['pass'], '')
        self.assertEqual(self.service.states['192.0.2.10'].slots[0]['user'], 'old.one')

    def test_duplicate_ip_does_not_send_twice(self):
        row = self.targets('192.0.2.10')[0]
        plans = self.prepare(targets=[row, row])
        self.assertEqual(len(plans), 1)
        self.execute([plans[0], plans[0]])
        self.assertEqual(len(self.service.writes), 1)

    def test_suffix_only_preserves_common_name_from_fresh_config(self):
        plans = self.prepare(targets=self.targets('192.0.2.10', '192.0.2.2'),
                             edits=[PoolEdit(0, worker=WorkerNaming(None, width=3))])
        self.assertEqual({p.status for p in plans}, {'ready'})
        by_ip = {p.ip: p for p in plans}
        self.assertEqual(by_ip['192.0.2.2'].changes[0].after, 'old.001')
        self.assertEqual(by_ip['192.0.2.10'].changes[0].after, 'old.002')
        results = self.execute(plans)
        self.assertEqual({r.status for r in results.values()}, {'succeeded'})

    def test_suffix_only_rejects_different_base_names_in_a_group(self):
        self.service.states['192.0.2.2'].slots[0]['user'] = 'other.previous'
        plans = self.prepare(targets=self.targets('192.0.2.10', '192.0.2.2'),
                             edits=[PoolEdit(0, worker=WorkerNaming(None))])
        self.assertEqual({p.status for p in plans}, {'unsupported'})
        self.assertTrue(all(p._prepared is None for p in plans))
        self.assertFalse(self.service.writes)

    def test_suffix_only_rejects_duplicate_last_octet_after_fresh_read(self):
        plans = self.prepare(targets=self.targets('192.0.2.10', '198.51.100.10'),
                             edits=[PoolEdit(0, worker=WorkerNaming(None, 'last_octet'))])
        self.assertEqual({p.status for p in plans}, {'unsupported'})
        self.assertFalse(self.service.writes)

    def test_url_preview_strips_existing_embedded_secret_without_changing_private_state(self):
        self.service.states['192.0.2.10'].slots[0]['url'] = 'stratum+tcp://private-user:url-secret@pool.invalid:3333'
        plan = self.prepare(edits=[PoolEdit(0, url='stratum+tcp://new.invalid:3333')])[0]
        self.assertEqual(plan.status, 'ready')
        self.assertEqual(plan.changes[0].before, 'stratum+tcp://pool.invalid:3333')
        self.assertNotIn('url-secret', repr(plan))

    def test_readback_updates_only_public_record_display(self):
        self.execute(self.prepare())
        record = self.service.records['192.0.2.10']
        self.assertEqual(record.display['Worker'], 'TEST.1')
        self.assertNotIn('private-one', repr(record))

    def test_parallel_consumers_of_same_plan_share_one_write(self):
        plans = self.prepare()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.execute, plans) for _ in range(2)]
            results = [f.result()['192.0.2.10'] for f in futures]
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0].status, 'succeeded')
        self.assertEqual(len(self.service.writes), 1)

    def test_conflicting_duplicate_selection_is_rejected_before_io(self):
        row = self.targets('192.0.2.10')[0]
        other = dict(row, DeviceId='other-device')
        with self.assertRaisesRegex(ValueError, 'разные устройства'):
            self.prepare(targets=[row, other])
        self.assertEqual(self.service.polls, 0)

    def test_dedicated_evidence_read_never_creates_an_executable_plan(self):
        from miner_scanner.pool_evidence import configuration_evidence
        with patch('miner_scanner.pool_evidence_readers.read_evidence',
                   return_value=configuration_evidence(config().slots, 'cgminer.pools', reading_kind='runtime')):
            plans = list(collect_pool_evidence_batch(self.service, self.targets('192.0.2.10')))
        self.assertEqual(plans[0].status, 'observed')
        self.assertIsNone(plans[0]._prepared)
        self.assertFalse(plans[0].changes)
        self.assertEqual(plans[0].evidence['reading_kind'], 'runtime')
        self.assertEqual(self.service.repository._db().execute('SELECT count(*) FROM commands').fetchone()[0], 0)
        self.assertFalse(self.service.writes)
        self.assertEqual(self.execute(plans)['192.0.2.10'].status, 'skipped')
        self.assertFalse(self.service.writes)

    def test_read_only_evidence_partial_failure_does_not_abort_other_devices(self):
        def read(service, record, transport):
            if transport.ip == '192.0.2.2':
                raise requests.ReadTimeout('private-response-data')
            return {'read_source': 'stock.cgi'}
        with patch('miner_scanner.pool_evidence_readers.read_evidence', side_effect=read):
            plans = list(collect_pool_evidence_batch(self.service, self.targets('192.0.2.10', '192.0.2.2')))
        by_ip = {p.ip: p for p in plans}
        self.assertEqual(by_ip['192.0.2.2'].status, 'offline')
        self.assertEqual(by_ip['192.0.2.10'].status, 'observed')
        self.assertNotIn('private-response-data', repr(plans))
        self.assertFalse(self.service.writes)

    def test_cancelled_evidence_read_releases_command_lock_without_network(self):
        cancel = Event()
        cancel.set()
        with patch('miner_scanner.pool_evidence_readers.read_evidence') as read:
            plans = list(collect_pool_evidence_batch(self.service, self.targets('192.0.2.10'), cancel=cancel))
        read.assert_not_called()
        self.assertEqual(plans[0].status, 'cancelled')
        self.assertTrue(self.service.guards['192.0.2.10'].acquire(False))
        self.service.guards['192.0.2.10'].release()
        self.assertFalse(self.service.writes)


if __name__ == '__main__':
    unittest.main()
