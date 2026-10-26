"""Offline controller checks: review, explicit send, structured results, retry."""
import importlib.util
import os
import time
import unittest
from unittest.mock import Mock, patch

from miner_scanner.models import CommandResult
from miner_scanner.preflight import PreflightResult

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in ('PyQt6', 'pandas', 'fpdf')),
                     'Desktop dependencies not installed')
class BulkCommandControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gemini_gui
        cls.gui = gemini_gui
        cls.app = gemini_gui.QApplication.instance() or gemini_gui.QApplication([])

    def setUp(self):
        from desktop_ui.i18n import set_language
        set_language('ru')
        self.enterContext(patch('requests.sessions.Session.request', side_effect=AssertionError('Unexpected network')))
        self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('Unexpected network')))
        self.service = Mock()
        self.service.get_record.return_value = None
        self.service.poll.return_value = None
        self.enterContext(patch.object(self.gui, 'default_service', return_value=self.service))

    def drain(self, window):
        deadline = time.monotonic() + 5
        while getattr(window, 'workers', []) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.002)
        self.app.processEvents()
        self.assertFalse(getattr(window, 'workers', []), 'A GUI worker did not finish')

    def window(self):
        from miner_scanner.access import AccessProfiles
        with patch.object(self.gui.AccessStore, 'load', return_value=AccessProfiles([])):
            window = self.gui.GeminiApp(settings={'check_updates': False}, ranges=[])
        def close():
            for worker in getattr(window, 'workers', []):
                worker.stop()
            self.drain(window)
            dialog = window.command_batch_dialog
            if dialog is not None:
                if dialog.phase == 'checking':
                    dialog.finish_preflight()
                if dialog.phase == 'executing':
                    dialog.finish_execution()
                dialog.close()
            window.close()
        self.addCleanup(close)
        return window

    def test_review_blocks_writes_until_send_and_retry_rechecks_only_safe_failed_targets(self):
        from PyQt6.QtCore import Qt
        window = self.window()
        rows = [dict(IP=f'192.0.2.{i}', Model=f'Demo {i}', Firmware='Stock',
                     Status='Running', DeviceId=f'device-{i}', Identity={'fingerprint': f'fp-{i}'})
                for i in (1, 2, 3)]
        window.on_result(rows)
        for row in range(window.table.rowCount()):
            window.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
        reviews, executions = [], []

        def review(service, targets, action, **kwargs):
            reviews.append([row['IP'] for row in targets])
            for row in targets:
                state = 'unsupported' if row['IP'] == '192.0.2.3' else 'ready'
                yield PreflightResult(row['IP'], state, state, row)

        def execute(service, targets, action, **kwargs):
            executions.append([row['IP'] for row in targets])
            for row in targets:
                success = row['IP'] == '192.0.2.1'
                kwargs['on_dispatch'](row['IP'], success)
                yield row['IP'], CommandResult(row['IP'], 'succeeded' if success else 'failed',
                    'Verified' if success else 'API отклонил команду', success)

        with patch('miner_scanner.preflight.preflight_batch', side_effect=review), \
             patch.object(self.gui, 'execute_batch', side_effect=execute):
            window.run_action('sleep')
            self.drain(window)
            dialog = window.command_batch_dialog
            self.assertEqual(dialog.phase, 'prepared')
            self.assertEqual(executions, [])
            self.assertEqual(len(dialog.ready_targets()), 2)
            dialog.start_button.click()
            dialog.start_button.click()
            self.drain(window)
            self.assertEqual(executions, [['192.0.2.1', '192.0.2.2']])
            self.assertEqual(dialog.phase, 'finished')
            statuses = {entry.ip: entry.result_status for entry in dialog.model.entries}
            self.assertEqual(statuses, {'192.0.2.1': 'succeeded', '192.0.2.2': 'failed', '192.0.2.3': ''})
            self.assertEqual([row['IP'] for row in dialog.retry_targets()], ['192.0.2.2'])
            # A retry uses the original failed scope, regardless of current table selection.
            window.table.item(2, 0).setCheckState(Qt.CheckState.Checked)
            dialog.retry_button.click()
            self.drain(window)
            retry = window.command_batch_dialog
            self.assertIsNot(retry, dialog)
            self.assertEqual(reviews, [['192.0.2.1', '192.0.2.2', '192.0.2.3'], ['192.0.2.2']])
            self.assertEqual(retry.phase, 'prepared')
            self.assertEqual(len(executions), 1, 'Retry must not skip review/confirmation')
            retry.close()
            window.show_command_results()
            self.assertTrue(retry.isVisible())

    def test_closed_prepared_review_does_not_block_a_new_action(self):
        window = self.window()
        row = dict(IP='192.0.2.1', Model='Demo', DeviceId='one')
        def review(service, targets, action, **kwargs):
            yield PreflightResult(row['IP'], 'ready', 'ready', row)
        with patch('miner_scanner.preflight.preflight_batch', side_effect=review):
            window.prepare_command_batch([row], 'sleep')
            self.drain(window)
            first = window.command_batch_dialog
            first.close()
            window.prepare_command_batch([row], 'led_on')
            self.drain(window)
            self.assertEqual(window.command_batch_dialog.action, 'led_on')

    def test_worker_emits_one_safe_structured_outcome_per_ip_if_queue_breaks(self):
        rows = [dict(IP=f'192.0.2.{i}') for i in (1, 2, 3)]
        def broken(service, targets, action, **kwargs):
            kwargs['on_dispatch'](rows[0]['IP'], True)
            yield rows[0]['IP'], CommandResult('one', 'succeeded', 'verified', True)
            kwargs['on_dispatch'](rows[1]['IP'], True)
            raise RuntimeError('password=must-not-appear')
        worker = self.gui.ActionWorker(rows, 'reboot')
        results, logs = [], []
        worker.command_result_signal.connect(lambda ip, result: results.append((ip, result)))
        worker.log_signal.connect(logs.append)
        with patch.object(self.gui, 'execute_batch', side_effect=broken):
            worker.run()
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0][1].status, 'succeeded')
        self.assertTrue(all(result.status == 'unconfirmed' for _, result in results[1:]))
        self.assertTrue(results[1][1].accepted_by_api)
        self.assertFalse(results[2][1].accepted_by_api)
        self.assertNotIn('must-not-appear', '\n'.join(logs))

    def test_closing_main_window_cannot_reenable_send_or_create_another_worker(self):
        from PyQt6.QtGui import QCloseEvent
        window = self.window()
        row = dict(IP='192.0.2.1', Model='Demo', DeviceId='one')
        with patch.object(self.gui, 'PreflightWorker') as review, patch.object(self.gui, 'ActionWorker') as action:
            review.return_value.isRunning.return_value = True
            window.prepare_command_batch([row], 'sleep')
            dialog = window.command_batch_dialog
            dialog.add_preflight(PreflightResult(row['IP'], 'ready', 'ready', row))
            event = QCloseEvent()
            with patch.object(self.gui.QTimer, 'singleShot'):
                window.closeEvent(event)
            self.assertFalse(event.isAccepted())
            self.assertTrue(window._closing_requested)
            dialog.finish_preflight()
            self.assertEqual(dialog.phase, 'finished')
            self.assertFalse(dialog.start_button.isEnabled())
            dialog.start_button.click()
            window.start_command_batch(dialog, [row], 'sleep')
            window.prepare_command_batch([row], 'reboot')
            action.assert_not_called()
            self.assertEqual(review.call_count, 1)
            review.return_value.isRunning.return_value = False
            window.workers.clear()

    def test_existing_results_rows_resize_when_table_font_changes(self):
        window = self.window()
        row = dict(IP='192.0.2.1', Model='Demo', DeviceId='one')
        with patch('miner_scanner.preflight.preflight_batch', return_value=iter([
                PreflightResult(row['IP'], 'ready', 'ready', row)])):
            window.prepare_command_batch([row], 'sleep')
            self.drain(window)
        dialog = window.command_batch_dialog
        window.app_settings['table_font_size'] = 24
        window.apply_theme()
        self.assertGreaterEqual(dialog.table.rowHeight(0), dialog.table.fontMetrics().height() + 10)

    def test_preflight_worker_cancellation_is_forwarded_without_control_requests(self):
        row = dict(IP='192.0.2.1')
        worker = self.gui.PreflightWorker([row], 'sleep')
        worker.stop()
        def cancelled(service, targets, action, **kwargs):
            self.assertIs(kwargs['cancel'], worker.cancel)
            self.assertTrue(kwargs['cancel'].is_set())
            yield PreflightResult(row['IP'], 'cancelled', 'cancelled', row)
        results = []
        worker.result_signal.connect(results.append)
        with patch('miner_scanner.preflight.preflight_batch', side_effect=cancelled):
            worker.run()
        self.assertEqual(results[0].status, 'cancelled')
        self.service.poll.assert_not_called()
