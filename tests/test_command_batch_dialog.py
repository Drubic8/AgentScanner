"""Offline Qt checks of batch review, per-device outcomes and safe retry."""
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


@unittest.skipUnless(importlib.util.find_spec('PyQt6'), 'Desktop dependencies not installed')
class CommandBatchDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtGui import QFontDatabase
        cls.app = QApplication.instance() or QApplication([])
        directory = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
        for filename in ('segoeui.ttf', 'segoeuib.ttf'):
            QFontDatabase.addApplicationFont(str(directory / filename))

    def setUp(self):
        from desktop_ui.i18n import set_language
        from desktop_ui.theme import apply_theme
        set_language('ru')
        apply_theme(self.app)
        self.addCleanup(apply_theme, self.app)
        self.addCleanup(set_language, 'ru')

    def dialog(self, targets=None, action='sleep'):
        from desktop_ui.command_batch_dialog import CommandBatchDialog
        if targets is None:
            targets = [dict(IP=f'192.0.2.{index}', Model='Antminer T21', Firmware='Stock',
                            DeviceId=f'device-{index}', Identity={'fingerprint': f'original-{index}'})
                       for index in range(1, 4)]
        dialog = CommandBatchDialog(targets, action)

        def close():
            dialog.finish_preflight()
            dialog.finish_execution()
            dialog.close()
        self.addCleanup(close)
        return dialog

    @staticmethod
    def checked(dialog, ip, status='ready', message='API compatible'):
        dialog.add_preflight(SimpleNamespace(ip=ip, status=status, message=message,
            target={'IP': ip}, record={'IP': ip, 'DeviceId': 'replacement-not-authorized'}))

    @staticmethod
    def result(status, message='result', accepted=False):
        from miner_scanner.models import CommandResult
        return CommandResult('test-command', status, message, accepted)

    def test_send_is_explicit_and_only_ready_original_snapshots_are_selected(self):
        dialog = self.dialog()
        starts = []
        dialog.start_requested.connect(starts.append)
        self.assertTrue(dialog.active)
        self.assertFalse(dialog.start_button.isEnabled())
        self.checked(dialog, '192.0.2.1')
        self.checked(dialog, '192.0.2.2', 'auth_required', 'Password required')
        self.checked(dialog, '192.0.2.3', 'unsupported', 'No low mode')
        self.assertFalse(dialog.start_button.isEnabled())
        dialog.finish_preflight()
        self.assertFalse(dialog.active)
        self.assertTrue(dialog.start_button.isEnabled())
        self.assertEqual(starts, [])
        self.assertIn('Готово: 1', dialog.summary.text())
        self.assertIn('Не готовы: 2', dialog.summary.text())
        dialog.start_button.click()
        dialog.start_button.click()
        self.assertEqual(len(starts), 1)
        self.assertEqual([row['IP'] for row in starts[0]], ['192.0.2.1'])
        self.assertEqual(starts[0][0]['DeviceId'], 'device-1')
        self.assertEqual(starts[0][0]['Identity']['fingerprint'], 'original-1')
        self.assertTrue(dialog.active)
        self.assertFalse(dialog.model.entries[1].attempted)

    def test_outcomes_acceptance_and_safe_retry_remain_separate(self):
        targets = [dict(IP=f'192.0.2.{index}', Model='Demo') for index in range(1, 9)]
        dialog = self.dialog(targets)
        for target in targets:
            self.checked(dialog, target['IP'])
        dialog.finish_preflight()
        dialog.start_button.click()
        results = [self.result('succeeded', accepted=True),
                   self.result('failed', 'API отклонил команду'),
                   self.result('unconfirmed', accepted=True),
                   self.result('failed', 'Ошибка ответа после записи'),
                   self.result('skipped'), self.result('cancelled'),
                   self.result('failed', 'API отклонил команду', accepted=True),
                   self.result('unsupported')]
        for target, result in zip(targets, results):
            dialog.mark_dispatched(target['IP'], result.accepted_by_api)
            dialog.set_result(target['IP'], result)
        dialog.finish_execution()
        self.assertIn('Принято API: 3', dialog.delivery_summary.text())
        self.assertIn('Подтверждено: 1', dialog.delivery_summary.text())
        self.assertIn('Итогов: 8/8', dialog.delivery_summary.text())
        self.assertEqual([row['IP'] for row in dialog.retry_targets()],
                         ['192.0.2.2', '192.0.2.5', '192.0.2.6'])
        retries = []
        dialog.retry_requested.connect(retries.append)
        dialog.retry_button.click()
        dialog.retry_button.click()
        self.assertEqual(len(retries), 1)
        self.assertEqual([row['IP'] for row in retries[0]], ['192.0.2.2', '192.0.2.5', '192.0.2.6'])

    def test_active_close_requests_stop_once_and_keeps_window_alive(self):
        dialog = self.dialog()
        stops = []
        dialog.cancel_requested.connect(lambda: stops.append(True))
        dialog.show()
        self.app.processEvents()
        dialog.close()
        dialog.reject()
        self.assertEqual(stops, [True])
        self.assertTrue(dialog.isVisible())
        self.assertFalse(dialog.cancel_button.isEnabled())
        dialog.finish_preflight()
        self.assertEqual(dialog.model.entries[0].preflight_status, 'cancelled')
        self.assertFalse(dialog.start_button.isEnabled())
        dialog.close()
        self.assertFalse(dialog.isVisible())

    def test_missing_final_result_is_unconfirmed_and_cannot_be_replayed(self):
        dialog = self.dialog()
        for entry in dialog.model.entries:
            self.checked(dialog, entry.ip)
        dialog.finish_preflight()
        dialog.start_button.click()
        dialog.finish_execution()
        self.assertTrue(all(entry.result_status == 'unconfirmed' for entry in dialog.model.entries))
        self.assertEqual(dialog.retry_targets(), [])
        self.assertFalse(dialog.retry_button.isEnabled())

    def test_result_details_are_plain_redacted_text_and_filter_does_not_change_targets(self):
        from PyQt6.QtCore import Qt
        dialog = self.dialog()
        self.checked(dialog, '192.0.2.1', message='checked without write')
        self.checked(dialog, '192.0.2.2', 'offline', 'no connection')
        self.checked(dialog, '192.0.2.3', 'unsupported', 'not compatible')
        dialog.finish_preflight()
        dialog.start_button.click()
        dialog.set_result('192.0.2.1', self.result('unconfirmed', '<b>device fault</b> token=super-secret'))
        dialog.finish_execution()
        dialog.table.selectRow(0)
        self.app.processEvents()
        self.assertIn('checked without write', dialog.details.toPlainText())
        self.assertIn('<b>device fault</b>', dialog.details.toPlainText())
        self.assertNotIn('super-secret', dialog.details.toPlainText())
        tooltip = dialog.model.data(dialog.model.index(0, 6), Qt.ItemDataRole.ToolTipRole)
        self.assertIn('&lt;b&gt;', tooltip)
        self.assertNotIn('super-secret', tooltip)
        dialog.search.setText('192.0.2.2')
        self.assertEqual(dialog.proxy.rowCount(), 1)
        self.assertEqual(len(dialog.model.entries), 3)
        self.assertEqual(dialog.ready_targets()[0]['DeviceId'], 'device-1')

    def test_500_devices_update_by_ip_without_replacing_model_or_losing_blocked_rows(self):
        targets = [dict(IP=f'198.51.{index // 254}.{index % 254 + 1}', Model='Demo') for index in range(500)]
        dialog = self.dialog(targets + targets[:1])
        original_model = dialog.model
        self.assertEqual(dialog.model.rowCount(), 500)
        for index, target in enumerate(targets):
            self.checked(dialog, target['IP'], 'ready' if index % 2 == 0 else 'unsupported')
        dialog.finish_preflight()
        self.assertIs(dialog.model, original_model)
        self.assertEqual(len(dialog.ready_targets()), 250)
        dialog.search.setText(targets[-1]['IP'])
        starts = []
        dialog.start_requested.connect(starts.append)
        dialog.start_button.click()
        self.assertEqual(len(starts[0]), 250)
        dialog.set_result(targets[0]['IP'], self.result('succeeded', accepted=True))
        self.assertEqual(dialog.model.entries[dialog.model.by_ip[targets[0]['IP']]].result_status, 'succeeded')
        self.assertEqual(dialog.model.entries[dialog.model.by_ip[targets[1]['IP']]].preflight_status, 'unsupported')
        dialog.finish_execution()
        self.assertEqual(dialog.model.rowCount(), 500)

    def test_avalon_wakeup_consequence_is_visible_before_send(self):
        dialog = self.dialog([dict(IP='192.0.2.134', Model='Avalon', ProfileId='canaan.avalon')], 'wakeup')
        self.assertFalse(dialog.consequences.isHidden())
        self.assertIn('перезагрузку', dialog.consequences.text())
