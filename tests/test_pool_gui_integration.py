"""Application entry/shutdown for pool edits; all session and device I/O mocked."""
from copy import deepcopy
import importlib.util
import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
DESKTOP_AVAILABLE = all(importlib.util.find_spec(name) for name in ('PyQt6', 'pandas', 'fpdf'))


@unittest.skipUnless(DESKTOP_AVAILABLE, 'Desktop dependencies not installed')
class PoolGuiIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gemini_gui
        from PyQt6.QtCore import pyqtSignal
        from PyQt6.QtWidgets import QDialog
        cls.gui = gemini_gui
        cls.app = gemini_gui.QApplication.instance() or gemini_gui.QApplication([])

        class PoolStub(QDialog):
            completed = pyqtSignal()

            def __init__(self, service, targets, parent=None):
                super().__init__(parent)
                self.service = service
                self.targets = deepcopy(targets)
                self.active = False
                self.stop_requested = False

            def request_stop(self):
                self.stop_requested = True

        cls.stub = PoolStub

    def window(self):
        from miner_scanner.access import AccessProfiles
        with patch.object(self.gui.AccessStore, 'load', return_value=AccessProfiles([])):
            window = self.gui.GeminiApp(settings={}, ranges=[])
        self.addCleanup(window.close)
        self.addCleanup(self.app.processEvents)
        return window

    def test_entry_uses_checked_selection_once_and_releases_closed_dialog(self):
        from PyQt6.QtCore import Qt
        window = self.window()
        window.on_result([dict(IP='192.0.2.10', Model='First'), dict(IP='192.0.2.2', Model='Second')])
        for row in range(window.table.rowCount()):
            window.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
        with patch.object(self.gui, 'PoolSettingsDialog', self.stub):
            dialog = window.open_pool_settings()
            self.assertEqual({r['IP'] for r in dialog.targets}, {'192.0.2.10', '192.0.2.2'})
            self.assertIs(window.open_pool_settings(), dialog)
            dialog.close()
            self.assertIsNone(window.pool_settings_dialog)

    def test_open_without_checked_devices_does_not_create_or_read_anything(self):
        window = self.window()
        with patch.object(self.gui.QMessageBox, 'warning'), patch.object(self.gui, 'PoolSettingsDialog') as constructor:
            self.assertIsNone(window.open_pool_settings())
            constructor.assert_not_called()

    def test_shutdown_cancels_pool_work_and_keeps_parent_alive_until_thread_finishes(self):
        from PyQt6.QtGui import QCloseEvent
        from PyQt6.QtCore import Qt
        window = self.window()
        window.on_result([dict(IP='192.0.2.10', Model='First')])
        window.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
        with patch.object(self.gui, 'PoolSettingsDialog', self.stub):
            dialog = window.open_pool_settings()
        dialog.active = True
        event = QCloseEvent()
        with patch.object(self.gui.QTimer, 'singleShot'):
            window.closeEvent(event)
        self.assertFalse(event.isAccepted())
        self.assertTrue(dialog.stop_requested)
        dialog.active = False
        event = QCloseEvent()
        window.closeEvent(event)
        self.assertTrue(event.isAccepted())
        self.assertIsNone(window.pool_settings_dialog)

    def test_entry_is_present_in_shared_control_menu_in_both_languages(self):
        from desktop_ui.i18n import set_language
        from PyQt6.QtWidgets import QMenu
        window = self.window()
        self.addCleanup(set_language, 'ru')
        for language, label in (('ru', 'Настроить пулы и воркеры…'), ('en', 'Configure pools and workers…')):
            set_language(language)
            menu = QMenu(window)
            window.add_mining_actions(menu)
            self.assertIn(label, [a.text() for a in menu.actions()])
            menu.deleteLater()


if __name__ == '__main__':
    unittest.main()
