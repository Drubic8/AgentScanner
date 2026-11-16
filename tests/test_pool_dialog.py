"""Offline Qt coverage of pool review, field intent and cancellation ownership."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from threading import Event
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


@unittest.skipUnless(importlib.util.find_spec('PyQt6'), 'Desktop dependencies not installed')
class PoolSettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from desktop_ui.i18n import set_language
        set_language('ru')
        self.addCleanup(set_language, 'ru')

    def wait_for(self, condition):
        from PyQt6.QtTest import QTest
        deadline = time.monotonic() + 5
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(5)
        self.assertTrue(condition(), 'Qt worker did not finish')
        self.app.processEvents()

    def dialog(self, targets=None):
        from desktop_ui.pool_dialog import PoolSettingsDialog
        if targets is None:
            targets = [dict(IP=ip, DeviceId=ip, Model='Demo', Firmware='Stock', FirmwareVersion='test')
                       for ip in ('192.0.2.10', '192.0.2.2')]
        dialog = PoolSettingsDialog(object(), targets)

        def cleanup():
            dialog.request_stop()
            self.wait_for(lambda: not dialog.active)
            dialog.close()
            self.app.processEvents()
        self.addCleanup(cleanup)
        return dialog

    @staticmethod
    def plan(ip, *, status='ready', changes=(), message='Reviewed'):
        from miner_scanner.pool_configuration import PoolPlan
        return PoolPlan(ip=ip, status=status, changes=tuple(changes), message=message)

    def enable_worker(self, dialog):
        dialog.slots[0].worker.setChecked(True)
        dialog.worker_name.setText('TEST')

    def prepared(self, dialog, plans):
        with patch('desktop_ui.pool_dialog.prepare_pool_batch', return_value=iter(plans)) as prepare:
            dialog.start_preview()
            self.wait_for(lambda: not dialog.active)
            self.assertEqual(prepare.call_count, 1)

    def test_defaults_preserve_fields_and_constructor_does_no_io(self):
        with patch('desktop_ui.pool_dialog.prepare_pool_batch') as prepare:
            dialog = self.dialog()
            self.assertFalse(dialog.active)
            self.assertFalse(dialog.apply_button.isEnabled())
            self.assertFalse(dialog.export_button.isEnabled())
            for editor in dialog.slots:
                self.assertEqual(editor.url_mode.currentIndex(), 0)
                self.assertEqual(editor.password_mode.currentIndex(), 0)
                self.assertFalse(editor.worker.isChecked())
                self.assertFalse(editor.password.isEnabled())
            dialog.start_preview()
            self.assertIn('хотя бы одно', dialog.error.text())
            prepare.assert_not_called()

    def test_explicit_field_actions_preserve_other_slots_and_password(self):
        dialog = self.dialog()
        editor = dialog.slots[1]
        editor.url_mode.setCurrentIndex(1)
        editor.url.setText('stratum+tcp://new.example.invalid:3333')
        self.assertEqual([(edit.slot, edit.url, edit.worker, edit.password) for edit in dialog.edits()],
                         [(1, 'stratum+tcp://new.example.invalid:3333', None, None)])
        editor.url_mode.setCurrentIndex(2)
        editor.password_mode.setCurrentIndex(2)
        edit = dialog.edits()[0]
        self.assertEqual((edit.url, edit.password), ('', ''))
        editor.password_mode.setCurrentIndex(1)
        with self.assertRaises(ValueError):
            dialog.edits()
        self.assertEqual(dialog.tabs.currentIndex(), 1)

    def test_all_three_pool_passwords_are_sent_as_exact_independent_fields(self):
        from PyQt6.QtWidgets import QLineEdit
        dialog = self.dialog([dict(IP='192.0.2.2', DeviceId='wm-demo', Make='MicroBT',
                                   ProfileId='microbt.rpc3', Model='M61S', Firmware='Stock')])
        passwords = ('  first.pool.password  ', 'пароль.Åβ &+="', '')
        for slot, password in enumerate(passwords):
            editor = dialog.slots[slot]
            self.assertEqual(editor.password.echoMode(), QLineEdit.EchoMode.Password)
            editor.password_mode.setCurrentIndex(1 if password else 2)
            editor.password.setText(password if password else 'stale-input-must-not-be-sent')
        edits = dialog.edits()
        self.assertEqual([edit.password for edit in edits], list(passwords))
        self.assertTrue(all(edit.worker is None and edit.url is None for edit in edits))
        received = []

        def prepare(service, targets, edits, **kwargs):
            received.append([edit.password for edit in edits])
            yield self.plan('192.0.2.2', status='ready')

        with patch('desktop_ui.pool_dialog.prepare_pool_batch', side_effect=prepare):
            dialog.start_preview()
            self.wait_for(lambda: not dialog.active)
        self.assertEqual(received, [list(passwords)])

    def test_whatsminer_password_hint_and_preserve_mode_do_not_invent_values(self):
        dialog = self.dialog([dict(IP='192.0.2.2', DeviceId='wm-demo',
                                   Identity={'make': 'MicroBT', 'profile_id': 'microbt.rpc3'})])
        self.assertFalse(dialog.whatsminer_password_hint.isHidden())
        self.assertIn('каждого из трёх слотов', dialog.whatsminer_password_hint.text())
        self.assertIn('автоматической подстановки нет', dialog.whatsminer_password_hint.text())
        self.enable_worker(dialog)
        edits = dialog.edits()
        self.assertEqual(len(edits), 1)
        self.assertIsNone(edits[0].password)
        self.assertTrue(all(editor.password.text() == '' for editor in dialog.slots))
        self.assertTrue(self.dialog().whatsminer_password_hint.isHidden())

    def test_compact_whatsminer_form_keeps_password_controls_visible(self):
        from desktop_ui.theme import apply_theme
        apply_theme(self.app)
        self.addCleanup(apply_theme, self.app)
        dialog = self.dialog([dict(IP='192.0.2.2', Make='MicroBT', ProfileId='microbt.rpc3')])
        dialog.resize(820, 620)
        dialog.show()
        dialog.slots[0].password_mode.setCurrentIndex(1)
        self.app.processEvents()
        self.assertFalse(dialog.table.isVisible())
        self.assertFalse(dialog.details.isVisible())
        self.assertGreaterEqual(dialog.slots[0].password.height(), dialog.slots[0].password.sizeHint().height())
        self.assertGreater(dialog.slots[0].height(), 110)
        self.assertTrue(dialog.slots[0].password.isVisible())

    def test_english_pool_password_is_distinct_from_asic_access_password(self):
        from desktop_ui.i18n import set_language
        from PyQt6.QtWidgets import QLabel
        set_language('en')
        dialog = self.dialog([dict(IP='192.0.2.2', ProfileId='microbt.rpc3')])
        labels = [label.text() for label in dialog.slots[0].findChildren(QLabel)]
        self.assertIn('Pool password', labels)
        self.assertIn('ASIC access password separately', dialog.slots[0].password.toolTip())
        self.assertIn('does not return pool passwords', dialog.whatsminer_password_hint.text())

    def test_common_worker_name_and_suffix_controls_are_passed_to_core(self):
        dialog = self.dialog()
        self.enable_worker(dialog)
        dialog.start.setValue(5)
        dialog.width_spin.setValue(3)
        naming = dialog.edits()[0].worker
        self.assertEqual(naming.render('192.0.2.10', 0), 'TEST.005')
        dialog.naming_mode.setCurrentIndex(1)
        self.assertEqual(dialog.edits()[0].worker.render('192.0.2.10', 0), 'TEST.192_0_2_10')
        self.assertFalse(dialog.start.isEnabled())
        dialog.naming_mode.setCurrentIndex(2)
        self.assertEqual(dialog.edits()[0].worker.render('192.0.2.10', 0), 'TEST.10')
        dialog.worker_name.setText('TEST.bad')
        with self.assertRaises(ValueError):
            dialog.edits()

    def test_suffix_only_mode_keeps_name_and_disables_replacement_input(self):
        dialog = self.dialog()
        dialog.slots[0].worker.setChecked(True)
        dialog.keep_worker_name.setChecked(True)
        naming = dialog.edits()[0].worker
        self.assertIsNone(naming.name)
        self.assertFalse(dialog.worker_name.isEnabled())
        dialog.keep_worker_name.setChecked(False)
        self.assertTrue(dialog.worker_name.isEnabled())
        with self.assertRaises(ValueError):
            dialog.edits()

    def test_conflicting_duplicate_identity_is_not_silently_selected(self):
        from desktop_ui.pool_dialog import _ordered_targets
        with self.assertRaises(ValueError):
            _ordered_targets([{'IP': '192.0.2.2', 'DeviceId': 'first'},
                              {'IP': '192.0.2.2', 'DeviceId': 'second'}])
        self.assertEqual(len(_ordered_targets([{'IP': '192.0.2.2', 'DeviceId': 'first'},
                                              {'IP': '192.0.2.2', 'DeviceId': 'first'}])), 1)

    def test_preview_is_read_only_and_orders_ips_numerically(self):
        from miner_scanner.pool_configuration import PoolChange
        dialog = self.dialog()
        self.enable_worker(dialog)
        calls = []

        def prepare(service, targets, edits, **kwargs):
            calls.append([row['IP'] for row in targets])
            for index, row in enumerate(targets):
                yield self.plan(row['IP'], changes=[PoolChange(0, 'user', 'old', edits[0].worker.render(row['IP'], index))])

        with patch('desktop_ui.pool_dialog.prepare_pool_batch', side_effect=prepare), \
             patch('desktop_ui.pool_dialog.execute_pool_batch') as execute:
            dialog.start_preview()
            self.wait_for(lambda: not dialog.active)
            self.assertEqual(calls, [['192.0.2.2', '192.0.2.10']])
            self.assertEqual([row[6] for row in dialog.model.rows], ['TEST.1', 'TEST.2'])
            self.assertTrue(dialog.apply_button.isEnabled())
            execute.assert_not_called()

    def test_form_edit_invalidates_review_and_disables_apply(self):
        dialog = self.dialog()
        self.enable_worker(dialog)
        self.prepared(dialog, [self.plan('192.0.2.2')])
        self.assertTrue(dialog.apply_button.isEnabled())
        dialog.worker_name.setText('UPDATED')
        self.assertEqual(dialog.phase, 'editing')
        self.assertFalse(dialog.apply_button.isEnabled())
        self.assertEqual(dialog.plans, {})

    def test_password_values_never_enter_display_model(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QLineEdit
        from miner_scanner.pool_configuration import PoolChange
        dialog = self.dialog()
        editor = dialog.slots[0]
        editor.password_mode.setCurrentIndex(1)
        editor.password.setText('input-secret')
        self.assertEqual(editor.password.echoMode(), QLineEdit.EchoMode.Password)
        # Defend even against a malformed public change from a future backend.
        self.prepared(dialog, [self.plan('192.0.2.2', changes=[PoolChange(0, 'pass', 'old-secret', 'new-secret')])])
        display = ' '.join(str(dialog.model.data(dialog.model.index(row, column), role))
                           for row in range(dialog.model.rowCount())
                           for column in range(dialog.model.columnCount())
                           for role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole))
        for secret in ('input-secret', 'old-secret', 'new-secret'):
            self.assertNotIn(secret, display)
        self.assertIn('Пароль', display)

    def test_apply_only_ready_once_and_keeps_partial_results(self):
        from miner_scanner.models import CommandResult
        dialog = self.dialog()
        self.enable_worker(dialog)
        dialog.slots[0].password_mode.setCurrentIndex(1)
        dialog.slots[0].password.setText('forget-after-execution')
        self.prepared(dialog, [self.plan('192.0.2.2'), self.plan('192.0.2.10', status='unsupported')])
        completed = []
        dialog.completed.connect(lambda: completed.append(True))
        received = []

        def execute(service, plans, **kwargs):
            received.append([plan.ip for plan in plans])
            yield '192.0.2.2', CommandResult('example', 'unconfirmed', 'No readback', True)

        with patch('desktop_ui.pool_dialog.execute_pool_batch', side_effect=execute):
            dialog.start_apply()
            dialog.start_apply()
            self.wait_for(lambda: not dialog.active)
            dialog.start_apply()
        self.assertEqual(received, [['192.0.2.2']])
        self.assertEqual(completed, [True])
        self.assertEqual(dialog.phase, 'finished')
        self.assertFalse(dialog.apply_button.isEnabled())
        self.assertEqual(dialog.plans, {})
        self.assertEqual(dialog.slots[0].password.text(), '')
        self.assertIn('192.0.2.2', dialog.results)
        self.assertEqual(dialog.model.rows[0][7], 'unconfirmed')
        self.assertEqual(dialog.model.rows[0][8], 'Да')
        self.assertEqual(dialog.model.rows[1][7], 'unsupported')

    def test_close_while_reading_cancels_but_retains_worker_ownership(self):
        dialog = self.dialog()
        self.enable_worker(dialog)
        entered = Event()

        def prepare(service, targets, edits, *, cancel, **kwargs):
            entered.set()
            cancel.wait(3)
            return
            yield  # This is a cancellable iterator, with no network operations.

        with patch('desktop_ui.pool_dialog.prepare_pool_batch', side_effect=prepare), \
             patch('desktop_ui.pool_dialog.execute_pool_batch') as execute:
            dialog.show()
            dialog.start_preview()
            self.wait_for(entered.is_set)
            dialog.close()
            self.assertTrue(dialog.isVisible())
            self.assertTrue(dialog.active)
            self.assertTrue(dialog._job.cancel.is_set())
            self.wait_for(lambda: not dialog.active)
            self.assertEqual(dialog.phase, 'finished')
            self.assertTrue(all(row[7] == 'cancelled' for row in dialog.model.rows))
            execute.assert_not_called()

    def test_missing_execution_result_is_uncertain_and_not_replayable(self):
        dialog = self.dialog()
        self.enable_worker(dialog)
        self.prepared(dialog, [self.plan('192.0.2.2')])
        with patch('desktop_ui.pool_dialog.execute_pool_batch', return_value=iter(())) as execute:
            dialog.start_apply()
            self.wait_for(lambda: not dialog.active)
            dialog.start_apply()
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(dialog.results['192.0.2.2'].status, 'unconfirmed')

    def test_validation_failure_displays_safe_message_and_no_transport_exception(self):
        dialog = self.dialog()
        self.enable_worker(dialog)
        with patch('desktop_ui.pool_dialog.prepare_pool_batch', side_effect=ValueError('Совпадают итоговые workers')):
            dialog.start_preview()
            self.wait_for(lambda: not dialog.active)
            self.assertIn('Совпадают', dialog.error.text())
        with patch('desktop_ui.pool_dialog.prepare_pool_batch', side_effect=RuntimeError('raw-private-response')):
            dialog.start_preview()
            self.wait_for(lambda: not dialog.active)
            self.assertNotIn('raw-private-response', dialog.error.text())
            self.assertFalse(dialog.apply_button.isEnabled())

    def test_english_interface_has_translated_local_controls(self):
        from desktop_ui.i18n import set_language
        set_language('en')
        dialog = self.dialog()
        self.assertEqual(dialog.windowTitle(), 'Pool and worker settings')
        self.assertIn('Read and preview', dialog.preview_button.text())
        self.assertEqual(dialog.slots[0].url_mode.itemText(0), 'Keep')
        self.assertEqual(dialog.model.headerData(7, __import__('PyQt6.QtCore', fromlist=['Qt']).Qt.Orientation.Horizontal), 'Result')

    def test_english_backend_reason_and_keyboard_details_are_translated(self):
        from desktop_ui.i18n import set_language
        from miner_scanner.pool_backends import unsupported_reason
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        set_language('en')
        dialog = self.dialog()
        self.enable_worker(dialog)
        reason = unsupported_reason(SimpleNamespace(identity=SimpleNamespace(
            make='Goldshell', firmware='Stock', profile_id='goldshell.mcb')))
        self.prepared(dialog, [self.plan('192.0.2.2', status='unsupported', message=reason),
                               self.plan('192.0.2.10', status='offline',
                                         message='Нет связи с устройством или истёк срок ожидания')])
        detail = dialog.model.data(dialog.model.index(0, 9))
        self.assertIn('Goldshell: MCB reads do not confirm', detail)
        self.assertFalse(any('\u0400' <= char <= '\u04ff' for char in detail))
        dialog.resize(820, 620)
        dialog.show()
        dialog.table.selectRow(0)
        self.app.processEvents()
        self.assertIn(detail, dialog.details.toPlainText())
        dialog.table.setFocus()
        QTest.keyClick(dialog.table, Qt.Key.Key_Down)
        self.app.processEvents()
        self.assertIn('192.0.2.10', dialog.details.toPlainText())
        self.assertIn('unreachable', dialog.details.toPlainText())
        self.assertTrue(dialog.details.isReadOnly())
        self.assertTrue(dialog.details.tabChangesFocus())

    def test_english_preview_conflict_and_worker_validation_are_translated(self):
        from desktop_ui.i18n import set_language
        set_language('en')
        dialog = self.dialog()
        self.enable_worker(dialog)
        conflict = 'Совпадающие workers у разных ASIC; выберите полный IP или нумерацию'
        with patch('desktop_ui.pool_dialog.prepare_pool_batch', side_effect=ValueError(conflict)):
            dialog.start_preview()
            self.wait_for(lambda: not dialog.active)
        self.assertIn('Different ASICs would have the same worker', dialog.error.text())
        self.assertFalse(dialog.apply_button.isEnabled())
        dialog.worker_name.setText('TEST BAD')
        with patch('desktop_ui.pool_dialog.prepare_pool_batch') as prepare:
            dialog.start_preview()
            self.assertEqual(dialog.error.text(), 'Invalid common worker name')
            prepare.assert_not_called()

    def test_details_are_literal_text_and_hide_sensitive_values(self):
        dialog = self.dialog()
        self.enable_worker(dialog)
        self.prepared(dialog, [self.plan('192.0.2.2', status='unsupported',
            message='<b>Literal response</b> token=private-secret')])
        dialog.table.selectRow(0)
        self.app.processEvents()
        self.assertIn('<b>Literal response</b>', dialog.details.toPlainText())
        self.assertNotIn('private-secret', dialog.details.toPlainText())

    def test_export_requires_a_review_and_explicit_file_selection(self):
        dialog = self.dialog()
        with patch('desktop_ui.pool_dialog.QFileDialog.getSaveFileName') as choose:
            dialog.export_evidence()
            choose.assert_not_called()
        self.enable_worker(dialog)
        self.prepared(dialog, [self.plan('192.0.2.2', status='unsupported')])
        self.assertTrue(dialog.export_button.isEnabled())
        with patch('desktop_ui.pool_dialog.QFileDialog.getSaveFileName', return_value=('', '')) as choose, \
             patch('desktop_ui.pool_dialog.evidence_report') as report, \
             patch('desktop_ui.pool_dialog.prepare_pool_batch') as read, \
             patch('desktop_ui.pool_dialog.execute_pool_batch') as write:
            dialog.export_evidence()
            choose.assert_called_once()
            self.assertEqual(choose.call_args.args[2], 'asic-pool-compatibility.json')
            report.assert_not_called()
            read.assert_not_called()
            write.assert_not_called()

    def test_export_uses_sanitized_snapshots_and_keeps_device_data_out_of_json(self):
        from dataclasses import replace
        from miner_scanner.pool_configuration import PoolChange
        dialog = self.dialog()
        self.enable_worker(dialog)
        plan = replace(self.plan('192.0.2.2', status='unsupported', changes=[
            PoolChange(0, 'url', 'stratum+tcp://private-pool.invalid:3333', 'stratum+tcp://replacement.invalid:3333'),
            PoolChange(0, 'user', 'private-worker', 'private-new-worker'),
            PoolChange(0, 'pass', 'private-old-password', 'private-new-password')]),
            model='Antminer S21', firmware='Stock', firmware_version='2026.01',
            device_id='private-device-id', command_id='private-command-id')
        self.prepared(dialog, [plan])
        # Even a later accidental addition to an in-memory snapshot must be
        # rejected by the export boundary, rather than serialized verbatim.
        dialog._evidence_by_ip[plan.ip]['password'] = 'private-added-password'
        dialog._evidence_by_ip[plan.ip]['ip'] = plan.ip
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / 'evidence.json'
            with patch('desktop_ui.pool_dialog.QFileDialog.getSaveFileName', return_value=(str(path), 'JSON (*.json)')), \
                 patch('desktop_ui.pool_dialog.prepare_pool_batch') as read, \
                 patch('desktop_ui.pool_dialog.execute_pool_batch') as write:
                dialog.export_evidence()
                text = path.read_text(encoding='utf-8')
                data = json.loads(text)
                self.assertEqual(len(data['devices']), 1)
                self.assertIn('1', dialog.export_status.text())
                for value in ('192.0.2.2', 'private-pool', 'replacement.invalid', 'private-worker',
                              'private-new-worker', 'private-old-password', 'private-new-password',
                              'private-device-id', 'private-command-id', 'private-added-password'):
                    self.assertNotIn(value, text)
                read.assert_not_called()
                write.assert_not_called()

    def test_export_after_execution_retains_only_safe_evidence_and_outcome(self):
        from miner_scanner.models import CommandResult
        dialog = self.dialog()
        self.enable_worker(dialog)
        self.prepared(dialog, [self.plan('192.0.2.2')])
        with patch('desktop_ui.pool_dialog.execute_pool_batch', return_value=iter([
                ('192.0.2.2', CommandResult('private-command', 'unconfirmed', 'No readback', True))])):
            dialog.start_apply()
            self.wait_for(lambda: not dialog.active)
        self.assertEqual(dialog.plans, {})
        self.assertTrue(dialog.export_button.isEnabled())
        self.assertEqual(dialog._evidence_by_ip['192.0.2.2']['result_status'], 'unconfirmed')
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / 'after.json'
            with patch('desktop_ui.pool_dialog.QFileDialog.getSaveFileName', return_value=(str(path), 'JSON (*.json)')):
                dialog.export_evidence()
                data = json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(data['devices'][0]['result_status'], 'unconfirmed')
        dialog.worker_name.setText('NEW')
        self.assertFalse(dialog.export_button.isEnabled())
        self.assertEqual(dialog._evidence_by_ip, {})

    def test_export_errors_are_fixed_and_do_not_overwrite_existing_file(self):
        dialog = self.dialog()
        self.enable_worker(dialog)
        self.prepared(dialog, [self.plan('192.0.2.2')])
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / 'existing.json'
            path.write_text('previous content', encoding='utf-8')
            with patch('desktop_ui.pool_dialog.QFileDialog.getSaveFileName', return_value=(str(path), 'JSON (*.json)')), \
                 patch('desktop_ui.pool_dialog.evidence_report', side_effect=ValueError('private-error-content')):
                dialog.export_evidence()
                self.assertEqual(path.read_text(encoding='utf-8'), 'previous content')
                self.assertIn('Не удалось сохранить', dialog.export_status.text())
                self.assertNotIn('private-error-content', dialog.export_status.text())
            missing = Path(scratch) / 'missing-directory' / 'failed.json'
            with patch('desktop_ui.pool_dialog.QFileDialog.getSaveFileName', return_value=(str(missing), 'JSON (*.json)')):
                dialog.export_evidence()
                self.assertFalse(missing.exists())
                self.assertIn('Не удалось сохранить', dialog.export_status.text())

    def test_evidence_read_needs_no_edits_and_cannot_apply_settings(self):
        dialog = self.dialog()
        dialog.resize(820, 620)
        dialog.show()
        plans = [self.plan('192.0.2.2', status='observed'),
                 self.plan('192.0.2.10', status='auth_required', message='Настройте доступ к ASIC перед изменением пулов')]
        self.assertTrue(dialog.evidence_button.isEnabled())
        with patch('desktop_ui.pool_dialog.collect_pool_evidence_batch', return_value=iter(plans)) as collect, \
             patch('desktop_ui.pool_dialog.prepare_pool_batch') as prepare, \
             patch('desktop_ui.pool_dialog.execute_pool_batch') as execute:
            dialog.evidence_button.click()
            self.wait_for(lambda: not dialog.active)
            collect.assert_called_once()
            self.assertTrue(dialog._read_only_evidence)
            self.assertEqual(dialog.error.text(), '')
            self.assertFalse(dialog.apply_button.isEnabled())
            self.assertTrue(dialog.export_button.isEnabled())
            self.assertIn('Только чтение', dialog.summary.text())
            self.assertEqual([row[7] for row in dialog.model.rows], ['observed', 'auth_required'])
            self.assertEqual(dialog.model.data(dialog.model.index(0, 7)), 'Сведения прочитаны')
            dialog.table.selectRow(0)
            self.app.processEvents()
            self.assertNotIn('Принято API', dialog.details.toPlainText())
            self.assertNotIn('Принято API', dialog.note.text())
            self.assertIn('Настройки ASIC не меняются', dialog.note.text())
            for column in (0, 1, 2, 7):
                self.assertFalse(dialog.table.isColumnHidden(column))
            for column in (3, 4, 5, 6, 8):
                self.assertTrue(dialog.table.isColumnHidden(column))
            dialog.start_apply()
            prepare.assert_not_called()
            execute.assert_not_called()

    def test_evidence_mode_guard_blocks_even_an_invalid_ready_result(self):
        dialog = self.dialog()
        with patch('desktop_ui.pool_dialog.collect_pool_evidence_batch', return_value=iter([self.plan('192.0.2.2')])), \
             patch('desktop_ui.pool_dialog.execute_pool_batch') as execute:
            dialog.start_evidence()
            self.wait_for(lambda: not dialog.active)
            self.assertFalse(dialog.apply_button.isEnabled())
            dialog.start_apply()
            execute.assert_not_called()

    def test_evidence_cancel_retains_partial_capture_and_worker_ownership(self):
        dialog = self.dialog()
        entered = Event()

        def collect(service, targets, *, cancel, **kwargs):
            yield self.plan('192.0.2.2', status='observed')
            entered.set()
            cancel.wait(3)

        with patch('desktop_ui.pool_dialog.collect_pool_evidence_batch', side_effect=collect), \
             patch('desktop_ui.pool_dialog.prepare_pool_batch') as prepare, \
             patch('desktop_ui.pool_dialog.execute_pool_batch') as execute:
            dialog.show()
            dialog.start_evidence()
            self.wait_for(lambda: entered.is_set() and bool(dialog._evidence_by_ip))
            self.assertFalse(dialog.preview_button.isEnabled())
            self.assertFalse(dialog.evidence_button.isEnabled())
            dialog.start_preview()
            dialog.close()
            self.assertTrue(dialog.active)
            self.assertTrue(dialog.isVisible())
            self.wait_for(lambda: not dialog.active)
            self.assertTrue(dialog.export_button.isEnabled())
            self.assertIn('Сведения прочитаны: 1', dialog.summary.text())
            self.assertEqual(dialog.model.rows[1][7], 'cancelled')
            self.assertFalse(dialog.apply_button.isEnabled())
            prepare.assert_not_called()
            execute.assert_not_called()

    def test_evidence_failure_is_read_only_and_keeps_received_evidence(self):
        dialog = self.dialog()

        def collect(*args, **kwargs):
            yield self.plan('192.0.2.2', status='unsupported')
            raise RuntimeError('private-response-content')

        with patch('desktop_ui.pool_dialog.collect_pool_evidence_batch', side_effect=collect), \
             patch('desktop_ui.pool_dialog.execute_pool_batch') as execute:
            dialog.start_evidence()
            self.wait_for(lambda: not dialog.active)
            self.assertIn('Настройки ASIC не изменялись', dialog.error.text())
            self.assertNotIn('private-response-content', dialog.error.text())
            self.assertTrue(dialog.export_button.isEnabled())
            self.assertFalse(dialog.apply_button.isEnabled())
            self.assertEqual(dialog.model.rows[1][7], 'failed')
            execute.assert_not_called()

    def test_observed_evidence_exports_without_additional_reads(self):
        dialog = self.dialog()
        with patch('desktop_ui.pool_dialog.collect_pool_evidence_batch', return_value=iter([self.plan('192.0.2.2', status='observed')])):
            dialog.start_evidence()
            self.wait_for(lambda: not dialog.active)
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / 'observed.json'
            with patch('desktop_ui.pool_dialog.QFileDialog.getSaveFileName', return_value=(str(path), 'JSON (*.json)')), \
                 patch('desktop_ui.pool_dialog.collect_pool_evidence_batch') as collect, \
                 patch('desktop_ui.pool_dialog.prepare_pool_batch') as prepare, \
                 patch('desktop_ui.pool_dialog.execute_pool_batch') as execute:
                dialog.export_evidence()
                data = json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(data['devices'][0]['review_status'], 'observed')
                self.assertNotIn('result_status', data['devices'][0])
                collect.assert_not_called()
                prepare.assert_not_called()
                execute.assert_not_called()

    def test_english_read_only_messages_and_notes_do_not_describe_writes(self):
        from desktop_ui.i18n import set_language
        set_language('en')
        dialog = self.dialog()
        plans = [self.plan('192.0.2.2', status='observed',
                          message='Сведения прочитаны без изменения настроек; совместимость записи не проверялась'),
                 self.plan('192.0.2.10', status='auth_required',
                           message='Нет доступа для чтения сведений о пулах; проверьте профиль доступа')]
        with patch('desktop_ui.pool_dialog.collect_pool_evidence_batch', return_value=iter(plans)):
            dialog.start_evidence()
            self.wait_for(lambda: not dialog.active)
        dialog.table.selectRow(0)
        self.app.processEvents()
        self.assertIn('write compatibility was not tested', dialog.details.toPlainText())
        self.assertNotIn('API accepted', dialog.details.toPlainText())
        self.assertIn('only reads technical evidence', dialog.note.text())
        dialog.table.selectRow(1)
        self.app.processEvents()
        self.assertIn('Pool evidence read access denied', dialog.details.toPlainText())
        self.assertFalse(any('\u0400' <= char <= '\u04ff' for char in dialog.details.toPlainText()))
        dialog.slots[0].worker.setChecked(True)
        self.assertIn('API acceptance and readback confirmation', dialog.note.text())

    def test_compact_review_shows_device_and_before_after_values(self):
        dialog = self.dialog()
        self.enable_worker(dialog)
        dialog.resize(820, 620)
        dialog.show()
        self.prepared(dialog, [self.plan('192.0.2.2')])
        self.assertFalse(dialog.settings.isVisible())
        self.assertGreater(dialog.table.height(), 180)
        self.assertTrue(dialog.table.isColumnHidden(1))
        for column in (0, 3, 4, 5, 6, 7, 8):
            self.assertFalse(dialog.table.isColumnHidden(column))
        header = dialog.table.horizontalHeader()
        self.assertEqual(header.sectionViewportPosition(3), header.sectionSize(0))


if __name__ == '__main__':
    unittest.main()
