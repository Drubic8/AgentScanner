"""Offline GUI checks for explicit AI consent, redacted input and worker lifetime."""
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


class MemoryStore:
    def __init__(self, key='key-private'):
        self.key = key
        self.saved = []
        self.deleted = 0

    def load(self):
        return self.key

    def save(self, value):
        self.saved.append(value)
        self.key = value

    def delete(self):
        self.deleted += 1
        self.key = ''


@unittest.skipUnless(importlib.util.find_spec('PyQt6'), 'Desktop dependencies not installed')
class AIInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        import desktop_ui.ai_panel as panel
        import desktop_ui.ai_settings_panel as settings
        cls.panel_module, cls.settings_module = panel, settings

    def setUp(self):
        from desktop_ui.i18n import set_language
        set_language('ru')
        self.addCleanup(set_language, 'ru')
        self.report = SimpleNamespace(display_text=lambda language='ru': '<b>Sensor alarm</b>\n[log:1] temperature failed')

    def wait(self, predicate, seconds=5):
        deadline = time.monotonic() + seconds
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.app.processEvents()
        self.assertTrue(predicate(), 'AI Qt worker did not retire')

    def clean(self, panel):
        panel.request_stop()
        self.wait(lambda: not panel.active)
        panel.close()

    def bundle(self, text='temperature failed\nworker=owner.private\n192.0.2.44'):
        from miner_scanner.device_logs import DeviceLogResult
        from miner_scanner.diagnostic_bundle import build_bundle
        return build_bundle(dict(IP='192.0.2.44', Model='Antminer S21', ProfileId='bitmain.stock',
            Worker='owner.private', Real='100 TH/s', Temp='70 71',
            Telemetry={'observed_at': '2026-11-01T10:00:00+00:00'}),
            DeviceLogResult('succeeded', text=text, source='fixture', kind='miner',
                collected_at='2026-11-01T10:01:00+00:00'), app_version='test', secrets=('password-private',))

    def panel(self, key='key-private', **kwargs):
        panel = self.panel_module.AIAnalysisPanel(config={'folder_id': 'folder-fixture'},
            store=MemoryStore(key), **kwargs)
        self.addCleanup(self.clean, panel)
        return panel

    def test_preview_is_redacted_and_opening_never_sends_a_request(self):
        with patch.object(self.panel_module, 'analyze') as request:
            panel = self.panel()
            panel.set_bundle(self.bundle())
            self.assertFalse(panel.analyze_button.isEnabled())
            panel.start_analysis()
            request.assert_not_called()
        self.assertEqual(panel.preview.toPlainText(), panel.prepared.preview)
        for private in ('owner.private', '192.0.2.44', 'key-private', 'password-private'):
            self.assertNotIn(private, panel.preview.toPlainText())
        self.assertIn('folder-fixture', panel.connection_summary.text())
        self.assertIn('yandexgpt-5.1', panel.connection_summary.text())

    def test_missing_key_disables_transmission(self):
        panel = self.panel(key='')
        panel.set_bundle(self.bundle())
        self.assertFalse(panel.consent.isEnabled())
        self.assertFalse(panel.analyze_button.isEnabled())

    def test_request_requires_new_consent_and_result_is_plain_text(self):
        panel = self.panel()
        panel.set_bundle(self.bundle())
        panel.consent.setChecked(True)
        with patch.object(self.panel_module, 'analyze', return_value=self.report) as request:
            panel.start_analysis()
            self.wait(lambda: not panel.active)
            self.assertEqual(request.call_count, 1)
            self.assertFalse(panel.consent.isChecked())
            panel.start_analysis()
            self.assertEqual(request.call_count, 1)
        self.assertIn('<b>Sensor alarm</b>', panel.result.toPlainText())
        panel.copy_report()
        self.assertEqual(self.app.clipboard().text(), panel.result.toPlainText())
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'assessment.txt'
            with patch.object(self.panel_module.QFileDialog, 'getSaveFileName', return_value=(str(path), '')):
                panel.save_report()
            self.assertEqual(path.read_text(encoding='utf-8'), panel.result.toPlainText())

    def test_cancelled_late_answer_is_discarded(self):
        panel = self.panel()
        panel.set_bundle(self.bundle())

        def late_result(prepared, config, key, *, cancel):
            cancel.wait(2)
            return self.report

        with patch.object(self.panel_module, 'analyze', side_effect=late_result):
            panel.consent.setChecked(True)
            panel.start_analysis()
            panel.request_stop()
            self.wait(lambda: not panel.active)
        self.assertIsNone(panel.report)
        self.assertEqual(panel.result.toPlainText(), '')
        self.assertIn('отменён', panel.status.text())

    def test_replaced_input_or_configuration_does_not_receive_old_answer(self):
        for change in ('input', 'config'):
            panel = self.panel()
            panel.set_bundle(self.bundle())

            def late_result(prepared, config, key, *, cancel):
                cancel.wait(2)
                return self.report

            with patch.object(self.panel_module, 'analyze', side_effect=late_result):
                panel.consent.setChecked(True)
                panel.start_analysis()
                if change == 'input':
                    panel.set_bundle(self.bundle('different event'))
                else:
                    panel.set_config({'folder_id': 'different-folder', 'model': 'aliceai-llm'})
                self.wait(lambda: not panel.active)
            self.assertIsNone(panel.report)
            self.assertEqual(panel.result.toPlainText(), '')
            self.assertFalse(panel.consent.isChecked())

    def test_errors_do_not_expose_arbitrary_exception_text(self):
        panel = self.panel()
        panel.set_bundle(self.bundle())
        with patch.object(self.panel_module, 'analyze', side_effect=RuntimeError('key-private and raw log')):
            panel.consent.setChecked(True)
            panel.start_analysis()
            self.wait(lambda: not panel.active)
        self.assertNotIn('key-private', panel.status.text())
        self.assertEqual(panel.result.toPlainText(), '')

    def test_device_close_waits_for_analysis_worker(self):
        from desktop_ui.device_dialog import DeviceDialog
        from miner_scanner.access import AccessProfiles
        dialog = DeviceDialog(dict(IP='192.0.2.44', Model='Fixture'), access_profiles=AccessProfiles([]),
            app_version='test', ai_config={'folder_id': 'folder-fixture'}, ai_store=MemoryStore())
        self.addCleanup(dialog.close)
        panel = dialog.ai_panel
        panel.set_bundle(self.bundle())

        def late_result(prepared, config, key, *, cancel):
            cancel.wait(2)
            return self.report

        dialog.show()
        with patch.object(self.panel_module, 'analyze', side_effect=late_result):
            panel.consent.setChecked(True)
            panel.start_analysis()
            self.assertTrue(dialog.active)
            self.assertFalse(dialog.open_button.isEnabled())
            dialog.reject()
            self.assertTrue(dialog.active)
            self.wait(lambda: not dialog.active)
        self.assertFalse(dialog.isVisible())
        self.assertIsNone(panel.report)

    def test_settings_cancel_and_validation_never_commit_a_key(self):
        from desktop_ui.settings_dialog import SettingsDialog
        from desktop_ui.preferences import defaults
        store = MemoryStore()
        dialog = SettingsDialog(defaults(), ai_store=store)
        dialog.ai_panel.key.setText('replacement-key')
        dialog.reject()
        self.assertEqual(store.saved, [])
        self.assertEqual(store.key, 'key-private')
        dialog = SettingsDialog(defaults(), ai_store=store)
        dialog.ai_panel.key.setText('replacement-key')
        dialog.ai_panel.folder.setText('invalid folder')
        dialog.save_and_close()
        self.assertEqual(store.saved, [])
        self.assertEqual(dialog.result(), 0)
        dialog.close()

    def test_settings_save_changes_key_without_putting_it_in_normal_settings(self):
        from desktop_ui.settings_dialog import SettingsDialog
        from desktop_ui.preferences import defaults
        store = MemoryStore()
        dialog = SettingsDialog(defaults(), ai_store=store)
        dialog.ai_panel.folder.setText('folder-fixture')
        dialog.ai_panel.key.setText('replacement-key')
        dialog.save_and_close()
        self.assertEqual(dialog.result(), 1)
        self.assertEqual(store.saved, ['replacement-key'])
        self.assertEqual(dialog.settings['ai']['folder_id'], 'folder-fixture')
        self.assertNotIn('replacement-key', str(dialog.settings))
        self.assertNotIn('key-private', str(dialog.settings))
        self.assertEqual(dialog.ai_panel.key.text(), '')
        dialog = SettingsDialog(defaults(), ai_store=store)
        dialog.ai_panel.remove_key.setChecked(True)
        dialog.save_and_close()
        self.assertEqual(store.deleted, 1)

    def test_connection_check_is_explicit_uses_unsaved_key_and_cancels_on_close(self):
        from desktop_ui.settings_dialog import SettingsDialog
        from desktop_ui.preferences import defaults
        store = MemoryStore()
        with patch.object(self.settings_module.YandexProvider, 'check_connection') as check:
            dialog = SettingsDialog(defaults(), ai_store=store)
            check.assert_not_called()
        panel = dialog.ai_panel
        panel.folder.setText('folder-fixture')
        panel.key.setText('unsaved-key')

        def check(*, cancel):
            cancel.wait(2)

        with patch.object(self.settings_module, 'YandexProvider') as provider:
            provider.return_value.check_connection.side_effect = check
            panel.check_connection()
            self.assertTrue(panel.active)
            dialog.reject()
            self.wait(lambda: not panel.active)
            self.assertEqual(provider.call_args.args[1], 'unsaved-key')
        self.assertEqual(store.saved, [])
        self.assertEqual(store.key, 'key-private')

    def test_english_labels_and_connection_summary(self):
        panel = self.panel(language='en')
        self.assertEqual(panel.analyze_button.text(), 'Analyze')
        self.assertIn('Folder:', panel.connection_summary.text())
        self.assertIn('Yandex AI Studio', panel.consent.text())
