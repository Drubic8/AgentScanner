"""Bulk command tests use only synthetic transports, never a real network."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from ipaddress import IPv4Address
from threading import Event, Lock
import time
import unittest
from unittest.mock import patch

from miner_scanner.batch import execute_batch
from miner_scanner.commands import dispatch_command, execute_command, PendingCommand
from miner_scanner.repository import DeviceRepository
from miner_scanner.profiles import ProfileRegistry
from miner_scanner.runtime import Operation
from miner_scanner.service import ScannerService
from tests.fakes import FakeFactory, FakeTransport


class ClosingTransport(FakeTransport):
    def __exit__(self, *_):
        self.factory.closed.append(self)


class ClosingFactory(FakeFactory):
    def __init__(self):
        super().__init__()
        self.closed = []

    def __call__(self, *args):
        return ClosingTransport(self, *args)


class CommandBatchTests(unittest.TestCase):
    def setUp(self):
        self.factory = ClosingFactory()
        repo = DeviceRepository(':memory:')
        self.addCleanup(repo.close)
        # A synthetic verified profile isolates scheduling from family discovery.
        profile = replace(ProfileRegistry().by_id['bitmain.stock'],
                          match={'models': ['Antminer S21'], 'firmware_versions': ['test-1']},
                          verified_commands=('identify_on', 'identify_off'))
        self.service = ScannerService(repository=repo, transport_factory=self.factory,
                                      registry=ProfileRegistry([profile]))

    def targets(self, count):
        return [self.service.poll(str(IPv4Address('198.18.0.1') + i)).to_legacy()
                for i in range(count)]

    def batch(self, rows, **kwargs):
        return execute_batch(self.service, rows, 'led_on',
                             experimental_ids=[row['DeviceId'] for row in rows], **kwargs)

    def test_500_dispatches_finish_while_verification_is_blocked(self):
        rows = self.targets(500)
        release_readback, all_written = Event(), Event()
        counter_lock = Lock()
        written, sessions = [], []

        def driver(transport, record, action, credentials):
            transport.http('/test-led', 'POST', payload={'blink': True})
            with counter_lock:
                written.append(record.identity.ip)
                sessions.append(transport)
                if len(written) == len(rows):
                    all_written.set()

            def verify():
                if not release_readback.wait(15):
                    raise TimeoutError('Test readback gate was not released')
                transport.operation.remaining()
                return True
            return True, verify

        dispatched = []
        with patch.dict('miner_scanner.commands.EXECUTORS', antminer=driver):
            with ThreadPoolExecutor(max_workers=1) as runner:
                future = runner.submit(lambda: list(self.batch(rows,
                    on_dispatch=lambda ip, accepted: dispatched.append((ip, accepted)))))
                try:
                    self.assertTrue(all_written.wait(10), 'Slow readback held up dispatch')
                    self.assertFalse(future.done(), 'Results must wait for readback')
                    self.assertEqual(len(self.factory.writes), 500)
                    # An in-flight request is durable uncertainty, not premature success.
                    self.assertTrue(self.service.command_lock_for(rows[-1]['IP']).locked())
                finally:
                    release_readback.set()
                results = future.result(timeout=10)
        self.assertEqual(len(results), 500)
        self.assertEqual(len(set(written)), 500)
        self.assertEqual(len(dispatched), 500)
        self.assertTrue(all(accepted for _, accepted in dispatched))
        self.assertTrue(all(result.status == 'succeeded' for _, result in results))
        self.assertTrue(all(session in self.factory.closed for session in sessions))
        self.assertTrue(all(not self.service.command_lock_for(row['IP']).locked() for row in rows))

    def test_opposite_command_waits_for_pending_verification_on_same_ip(self):
        row = self.targets(1)[0]
        writes = []
        def driver(transport, record, action, credentials):
            writes.append(action)
            return True, lambda: True
        with patch.dict('miner_scanner.commands.EXECUTORS', antminer=driver):
            first = dispatch_command(self.service, row['IP'], 'led_on', allow_unverified=True)
            self.assertIsInstance(first, PendingCommand)
            with ThreadPoolExecutor(max_workers=1) as runner:
                second = runner.submit(execute_command, self.service, row['IP'],
                                       'led_off', allow_unverified=True)
                try:
                    # The command lock stays held even between dispatch and verification.
                    self.assertTrue(self.service.command_lock_for(row['IP']).locked())
                    self.assertEqual(writes, ['identify_on'])
                finally:
                    self.assertEqual(first.complete().status, 'succeeded')
                self.assertEqual(second.result(timeout=5).status, 'succeeded')
        self.assertEqual(writes, ['identify_on', 'identify_off'])

    def test_cancel_after_write_never_replays_and_releases_resources(self):
        row = self.targets(1)[0]
        cancel, sessions = Event(), []
        def driver(transport, record, action, credentials):
            sessions.append(transport)
            transport.http('/test-led', 'POST', payload={'blink': True})
            return True, lambda: self.fail('Cancelled verification must not access the network')
        with patch.dict('miner_scanner.commands.EXECUTORS', antminer=driver):
            pending = dispatch_command(self.service, row['IP'], 'led_on', cancel=cancel,
                                       command_id='cancel-after-write', allow_unverified=True)
            self.assertIsInstance(pending, PendingCommand)
            cancel.set()
            result = pending.complete()
            repeated = execute_command(self.service, row['IP'], 'led_on',
                                       command_id='cancel-after-write', allow_unverified=True)
        self.assertEqual(result.status, 'unconfirmed')
        self.assertEqual(result, repeated)
        self.assertEqual(len(self.factory.writes), 1)
        self.assertIn(sessions[0], self.factory.closed)
        self.assertFalse(self.service.command_lock_for(row['IP']).locked())

    def test_closing_consumer_drains_and_closes_queued_commands(self):
        rows = self.targets(40)
        sessions = []
        def driver(transport, record, action, credentials):
            sessions.append(transport)
            return True, lambda: True
        with patch.dict('miner_scanner.commands.EXECUTORS', antminer=driver):
            iterator = self.batch(rows, workers=2)
            next(iterator)
            iterator.close()
        self.assertTrue(all(session in self.factory.closed for session in sessions))
        self.assertTrue(all(not self.service.command_lock_for(row['IP']).locked() for row in rows))

    def test_duplicate_ip_and_failure_do_not_block_other_targets(self):
        rows = self.targets(3)
        writes = []
        def driver(transport, record, action, credentials):
            writes.append(record.identity.ip)
            return record.identity.ip != rows[0]['IP'], lambda: True
        with patch.dict('miner_scanner.commands.EXECUTORS', antminer=driver):
            results = dict(self.batch([*rows, rows[1]]))
        self.assertEqual(len(writes), 3)
        self.assertEqual(results[rows[0]['IP']].status, 'failed')
        self.assertTrue(all(results[row['IP']].status == 'succeeded' for row in rows[1:]))

    def test_queue_wait_does_not_consume_verification_network_budget(self):
        row = self.targets(1)[0]
        with patch.dict('miner_scanner.commands.EXECUTORS', antminer=lambda t, *a:
                        (True, lambda: t.operation.remaining() > 0)):
            pending = dispatch_command(self.service, row['IP'], 'led_on', allow_unverified=True)
            pending.op.started = time.monotonic() - 3600
            self.assertEqual(pending.complete().status, 'succeeded')

    def test_accepted_without_state_change_is_not_success_and_write_is_once(self):
        rows = self.targets(4)
        writes = []
        def driver(transport, record, action, credentials):
            writes.append(record.identity.ip)
            return True, lambda: False
        with patch.dict('miner_scanner.commands.EXECUTORS', antminer=driver), \
             patch.object(Operation, 'pause', lambda *_: None):
            results = list(self.batch(rows))
        self.assertEqual(len(writes), 4)
        self.assertTrue(all(r.status == 'unconfirmed' and r.accepted_by_api for _, r in results))

    def test_cancelled_queue_sends_no_commands(self):
        rows = self.targets(4)
        cancel = Event()
        cancel.set()
        results = list(self.batch(rows, cancel=cancel))
        self.assertEqual(len(results), 4)
        self.assertTrue(all(r.status == 'cancelled' for _, r in results))
        self.assertFalse(self.factory.writes)

    def test_invalid_concurrency_is_rejected(self):
        for workers in [0, 33, True, '32']:
            with self.subTest(workers=workers), self.assertRaises(ValueError):
                list(self.batch([], workers=workers))
