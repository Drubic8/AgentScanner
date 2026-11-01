"""Offline Qt checks: device snapshots, log handling and worker lifetime."""
import importlib.util
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from zipfile import ZipFile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


@unittest.skipUnless(importlib.util.find_spec('PyQt6'), 'Desktop dependencies not installed')
class DeviceDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtGui import QFontDatabase
        import desktop_ui.device_dialog as module
        cls.module = module
        cls.app = QApplication.instance() or QApplication([])
        fonts = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
        for name in ('segoeui.ttf', 'segoeuib.ttf', 'consola.ttf'):
            QFontDatabase.addApplicationFont(str(fonts / name))

    def setUp(self):
        from desktop_ui.i18n import set_language
        set_language('ru')
        self.addCleanup(set_language, 'ru')

    def dialog(self, **changes):
        from miner_scanner.access import AccessProfile, AccessProfiles
        row = dict(IP='192.0.2.1', Model='Antminer T21', Firmware='Stock',
                   FirmwareVersion='fixture-1', ProfileId='bitmain.stock', Status='Running',
                   Real='100 TH/s', Avg='99 TH/s', Temp='65 66', Fan='4000 4100',
                   Uptime='1d 2h', Error='', Pool='stratum+tcp://pool.example.net:3333',
                   Worker='owner.rig-one', Pool3='stratum+tcp://third.example.net:3333',
                   Worker3='owner.rig-three', Identity={'serial': 'SERIAL-PRIVATE'},
                   Telemetry={'observed_at': '2026-10-31T08:00:00+00:00', 'stale': False},
                   Capabilities={'sleep': 'supported', 'reboot': 'unknown'})
        row.update(changes)
        profiles = AccessProfiles([AccessProfile('Fixture', 'antminer', 'root', 'password-private')])
        dialog = self.module.DeviceDialog(row, access_profiles=profiles, app_version='test')

        def cleanup():
            dialog.request_stop()
            self.wait_until(lambda: not dialog.active)
            dialog.close()
            self.app.processEvents()
        self.addCleanup(cleanup)
        return dialog, row

    def wait_until(self, condition, seconds=4):
        deadline = time.monotonic() + seconds
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.app.processEvents()
        self.assertTrue(condition(), 'Qt worker did not complete within the offline test budget')

    def read(self, dialog, text='chain[2]: temperature sensor failed\nno hashrate\n', **changes):
        result = self.module.DeviceLogResult('succeeded', text=text,
            source='/cgi-bin/log.cgi', collected_at='2026-10-31T09:00:00+00:00', **changes)
        with patch.object(self.module, 'read_device_log', return_value=result):
            dialog.fetch_log()
            self.wait_until(lambda: not dialog.active)
        return result

    def test_opening_card_is_read_only_and_snapshot_and_pool_holes_are_preserved(self):
        with patch.object(self.module, 'read_device_log') as read:
            dialog, row = self.dialog()
            self.app.processEvents()
            read.assert_not_called()
        row['Model'] = 'Changed'
        row['Telemetry']['observed_at'] = 'changed'
        self.assertEqual(dialog.row['Model'], 'Antminer T21')
        self.assertEqual(dialog.overview_values['ObservedAt'].text(), '2026-10-31T08:00:00+00:00')
        self.assertEqual(dialog.pool_table.item(1, 1).text(), '—')
        self.assertEqual(dialog.pool_table.item(2, 2).text(), 'owner.rig-three')
        self.assertNotIn('Перезагрузить', dialog.capabilities_label.text())
        self.assertIn('Сон', dialog.capabilities_label.text())
        self.assertFalse(dialog.active)
        self.assertTrue(dialog.export_button.isEnabled())

    def test_stale_values_are_not_displayed_as_current_measurements(self):
        dialog, _ = self.dialog(Stale=True)
        self.assertEqual(dialog.overview_values['State'].text(), 'Неизвестно')
        for key in ('Real', 'Avg', 'Uptime', 'Temp', 'Fan'):
            self.assertEqual(dialog.overview_values[key].text(), '—')
        for row in range(3):
            self.assertEqual(dialog.pool_table.item(row, 1).text(), '—')
            self.assertEqual(dialog.pool_table.item(row, 2).text(), '—')

    def test_waiting_and_stopping_use_localized_state_names(self):
        waiting, _ = self.dialog(Status='WaitWork')
        stopping, _ = self.dialog(Status='Stopping')
        self.assertEqual(waiting.overview_values['State'].text(), 'Ожидание')
        self.assertEqual(stopping.overview_values['State'].text(), 'Останавливается')

    def test_device_text_is_plain_and_search_wraps_without_rendering_html(self):
        from PyQt6.QtCore import Qt
        dialog, _ = self.dialog(Model='<img src="file:///private">', Error='<b>alarm</b>')
        self.assertEqual(dialog.heading.textFormat(), Qt.TextFormat.PlainText)
        self.assertEqual(dialog.overview_values['Error'].textFormat(), Qt.TextFormat.PlainText)
        text = '<script>do not execute</script>\nERROR chain 2\nERROR fan 1\n'
        self.read(dialog, text)
        self.assertEqual(dialog.log_text.toPlainText(), text)
        dialog.search.setText('ERROR')
        dialog.find_next()
        self.assertEqual(dialog.log_text.textCursor().selectedText(), 'ERROR')
        first = dialog.log_text.textCursor().selectionStart()
        dialog.find_next()
        self.assertGreater(dialog.log_text.textCursor().selectionStart(), first)
        dialog.find_next()
        self.assertEqual(dialog.log_text.textCursor().selectionStart(), first)
        dialog.find_next(backward=True)
        self.assertGreater(dialog.log_text.textCursor().selectionStart(), first)
        dialog.copy_log()
        self.assertEqual(self.app.clipboard().text(), 'ERROR')
        dialog.search.setText('not-present')
        dialog.find_next()
        self.assertEqual(dialog.notice.text(), 'Совпадений нет.')

    def test_failed_retry_clears_previous_log_and_does_not_export_it_as_new(self):
        dialog, _ = self.dialog()
        self.read(dialog, 'OLD SECRETLY STALE LOG')
        with patch.object(self.module, 'read_device_log',
                          return_value=self.module.DeviceLogResult('auth_required')):
            dialog.fetch_log()
            self.assertEqual(dialog.log_text.toPlainText(), '')
            self.assertFalse(dialog.export_button.isEnabled())
            self.wait_until(lambda: not dialog.active)
        self.assertEqual(dialog.log_text.toPlainText(), '')
        self.assertNotIn('OLD SECRETLY STALE LOG', dialog.preview.toPlainText())
        self.assertIn('Доступ к ASIC', dialog.log_status.text())
        self.assertFalse(dialog.copy_button.isEnabled())
        self.assertFalse(dialog.save_log_button.isEnabled())

    def test_empty_success_is_distinct_from_missing_log(self):
        dialog, _ = self.dialog()
        self.read(dialog, '')
        self.assertIn('пока пуст', dialog.log_status.text())
        self.assertIn('пока пуст', dialog.log_text.placeholderText())
        self.assertEqual(dialog.bundle['log']['text'], '')
        self.assertTrue(dialog.export_button.isEnabled())
        self.assertFalse(dialog.copy_button.isEnabled())

    def test_whatsminer_archive_wait_and_tail_are_explained_in_both_languages(self):
        from threading import Event
        from desktop_ui.i18n import set_language
        for language in ('ru', 'en'):
            with self.subTest(language=language):
                set_language(language)
                dialog, _ = self.dialog(ProfileId='microbt.rpc3', Model='WhatsMiner M61S+')
                self.assertTrue(dialog.fetch_button.isEnabled())
                self.assertEqual([dialog.kind_box.itemData(i) for i in range(dialog.kind_box.count())],
                                 ['miner', 'system'])
                self.assertIn('60', dialog.log_hint.text())
                dialog.kind_box.setCurrentIndex(1)
                started, release = Event(), Event()
                self.addCleanup(release.set)
                def read(row, kind, *, access_profiles, cancel):
                    started.set()
                    release.wait(2)
                    return self.module.DeviceLogResult('succeeded', text='LATEST SYSTEM LOG\n',
                        kind=kind, source='get.log.download/system.log', truncated=True)
                with patch.object(self.module, 'read_device_log', side_effect=read):
                    dialog.fetch_log()
                    self.wait_until(started.is_set)
                    self.assertIn('WhatsMiner', dialog.log_status.text())
                    self.assertIn('60', dialog.log_status.text())
                    self.assertFalse(dialog.kind_box.isEnabled())
                    release.set()
                    self.wait_until(lambda: not dialog.active)
                self.assertEqual(dialog.log_result.kind, 'system')
                self.assertIn('конец лога' if language == 'ru' else 'latest portion', dialog.log_status.text())
                self.assertEqual(dialog.log_text.toPlainText(), 'LATEST SYSTEM LOG\n')
                self.assertTrue(dialog.bundle['log']['truncated'])

    def test_whatsminer_archive_limit_does_not_claim_the_archive_is_limited_to_2_mib(self):
        dialog, _ = self.dialog(ProfileId='microbt.rpc3', Model='WhatsMiner M61S+')
        with patch.object(self.module, 'read_device_log', return_value=self.module.DeviceLogResult(
                'too_large', source='get.log.download/miner-state.log', kind='miner')):
            dialog.fetch_log()
            self.wait_until(lambda: not dialog.active)
        self.assertIn('Архив WhatsMiner', dialog.log_status.text())
        self.assertNotIn('Лог превышает ограничение 2', dialog.log_status.text())
        self.assertFalse(dialog.save_log_button.isEnabled())
        with patch.object(self.module, 'load_log_file', return_value=self.module.DeviceLogResult(
                'too_large', source='large.log', kind='imported')):
            dialog._start(path='unused-fake-path')
            self.wait_until(lambda: not dialog.active)
        self.assertIn('Лог превышает ограничение 2', dialog.log_status.text())

    def test_cancel_and_close_wait_for_worker_and_emit_idle_before_dialog_finished(self):
        from threading import Event
        from PyQt6.QtCore import QTimer
        started = Event()
        def blocking_read(row, kind, *, access_profiles, cancel):
            started.set()
            cancel.wait(2)
            return self.module.DeviceLogResult('cancelled', kind=kind)
        dialog, _ = self.dialog()
        order, responsive = [], []
        dialog.idle.connect(lambda: order.append(('idle', dialog.active)))
        dialog.finished.connect(lambda _: order.append(('closed', dialog.active)))
        dialog.show()
        with patch.object(self.module, 'read_device_log', side_effect=blocking_read):
            dialog.fetch_log()
            QTimer.singleShot(0, lambda: responsive.append(True))
            self.wait_until(started.is_set)
            self.app.processEvents()
            self.assertTrue(responsive)
            self.assertTrue(dialog.active)
            self.assertFalse(dialog.fetch_button.isEnabled())
            self.assertFalse(dialog.open_button.isEnabled())
            dialog.close()
            self.wait_until(lambda: not dialog.active)
        self.assertEqual(order, [('idle', False), ('closed', False)])
        self.assertFalse(dialog.isVisible())

    def test_escape_reject_requests_cancellation_without_destroying_running_worker(self):
        from threading import Event
        started, release = Event(), Event()
        def blocked(row, kind, *, access_profiles, cancel):
            started.set()
            release.wait(2)
            return self.module.DeviceLogResult('succeeded', text='late response', kind=kind)
        dialog, _ = self.dialog()
        with patch.object(self.module, 'read_device_log', side_effect=blocked):
            dialog.fetch_log()
            self.wait_until(started.is_set)
            dialog.reject()
            self.assertTrue(dialog.active)
            self.assertTrue(dialog._worker.cancel.is_set())
            release.set()
            self.wait_until(lambda: not dialog.active)
        self.assertEqual(dialog.log_result.status, 'cancelled')
        self.assertEqual(dialog.log_text.toPlainText(), '')

    def test_cancellation_after_thread_finishes_but_before_delivery_discards_success(self):
        dialog, _ = self.dialog()
        result = self.module.DeviceLogResult('succeeded', text='late log that must not be exported')
        with patch.object(self.module, 'read_device_log', return_value=result):
            dialog.fetch_log()
            # Intentionally do not pump Qt: finished is queued to this thread.
            self.assertTrue(dialog._worker.wait(2000))
            dialog.request_stop()
            self.wait_until(lambda: not dialog.active)
        self.assertEqual(dialog.log_result.status, 'cancelled')
        self.assertEqual(dialog.log_text.toPlainText(), '')
        self.assertNotIn('late log', dialog.preview.toPlainText())
        self.assertFalse(dialog.save_log_button.isEnabled())

    def test_import_available_for_unknown_device_and_preview_redacts_known_secrets(self):
        dialog, _ = self.dialog(ProfileId='generic.cgminer', Model='Unknown device')
        self.assertFalse(dialog.fetch_button.isEnabled())
        self.assertTrue(dialog.open_button.isEnabled())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'device.txt'
            path.write_text('chain 2 FAILED\nworker=owner.rig-one\n192.0.2.1\npassword=password-private\n', encoding='utf-8')
            with patch.object(self.module.QFileDialog, 'getOpenFileName', return_value=(str(path), '')):
                dialog.open_file()
                self.wait_until(lambda: not dialog.active)
        self.assertIn('chain 2 FAILED', dialog.log_text.toPlainText())
        preview = dialog.preview.toPlainText()
        self.assertIn('chain 2 FAILED', preview)
        for secret in ('owner.rig-one', '192.0.2.1', 'password-private', 'SERIAL-PRIVATE'):
            self.assertNotIn(secret, preview)
        self.assertEqual(dialog.log_result.kind, 'imported')
        self.assertIn('из файла', dialog.log_status.text())

    def test_redacted_text_and_zip_match_preview_and_do_not_overwrite(self):
        dialog, _ = self.dialog()
        self.read(dialog, 'chain 2 FAILED\npassword-private\nworker=owner.rig-one\n')
        with tempfile.TemporaryDirectory() as directory:
            text_path = Path(directory) / 'log.txt'
            zip_path = Path(directory) / 'bundle.zip'
            with patch.object(self.module.QFileDialog, 'getSaveFileName', return_value=(str(text_path), '')):
                dialog.save_log()
                saved = text_path.read_text(encoding='utf-8')
                self.assertEqual(saved, dialog.bundle['log']['text'])
                self.assertNotIn('password-private', saved)
                self.assertNotIn('owner.rig-one', saved)
                dialog.save_log()
                self.assertIn('уже существует', dialog.notice.text())
            with patch.object(self.module.QFileDialog, 'getSaveFileName', return_value=(str(zip_path), '')):
                dialog.save_bundle()
                with ZipFile(zip_path) as archive:
                    self.assertEqual(archive.read('device.log').decode('utf-8'), saved)
                dialog.save_bundle()
                self.assertIn('уже существует', dialog.notice.text())

    def test_export_disabled_when_redaction_fails_and_errors_do_not_echo_exception(self):
        dialog, _ = self.dialog()
        with patch.object(self.module, 'read_device_log', side_effect=RuntimeError('private-token')):
            dialog.fetch_log()
            self.wait_until(lambda: not dialog.active)
        self.assertNotIn('private-token', dialog.log_status.text())
        self.assertFalse(dialog.export_button.isEnabled())
        self.assertFalse(dialog.save_log_button.isEnabled())
        self.assertIn('Экспорт недоступен', dialog.preview.toPlainText())

    def test_english_card_has_english_controls_and_outcomes(self):
        from desktop_ui.i18n import set_language
        set_language('en')
        dialog, _ = self.dialog()
        self.assertEqual(dialog.tabs.tabText(0), 'Overview')
        self.assertEqual(dialog.fetch_button.text(), 'Fetch log')
        self.assertEqual(dialog.close_button.text(), 'Close')
        self.assertEqual(dialog.overview_values['State'].text(), 'Running')
        self.read(dialog)
        self.assertTrue(dialog.log_status.text().startswith('Log loaded'))


if __name__ == '__main__':
    unittest.main()
