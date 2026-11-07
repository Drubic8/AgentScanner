"""Device detail routing and application shutdown, with no network or user files."""
import importlib.util
import os
import unittest
from copy import deepcopy
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
DESKTOP_AVAILABLE = all(importlib.util.find_spec(name) for name in ('PyQt6', 'pandas', 'fpdf'))


@unittest.skipUnless(DESKTOP_AVAILABLE, 'Desktop dependencies not installed')
class DeviceCardIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gemini_gui
        from PyQt6.QtWidgets import QDialog
        cls.gui = gemini_gui
        cls.app = gemini_gui.QApplication.instance() or gemini_gui.QApplication([])

        class DetailStub(QDialog):
            def __init__(self, row, *, access_profiles, app_version, parent=None, ai_config=None):
                super().__init__(parent)
                self.row = deepcopy(row)
                self.access_profiles = access_profiles
                self.app_version = app_version
                self.ai_config = ai_config
                self.active = False
                self.stop_requested = False

            def request_stop(self):
                self.stop_requested = True

        cls.detail_stub = DetailStub

    def window(self):
        from miner_scanner.access import AccessProfiles
        with patch.object(self.gui.AccessStore, 'load', return_value=AccessProfiles([])):
            window = self.gui.GeminiApp(settings={}, ranges=[])
        self.addCleanup(window.close)
        self.addCleanup(self.app.processEvents)
        return window

    def test_card_uses_current_row_without_changing_checked_batch(self):
        from PyQt6.QtCore import Qt
        window = self.window()
        self.assertFalse(window.btn_device_card.isEnabled())
        window.on_result([dict(IP='192.0.2.1', Model='First'), dict(IP='192.0.2.2', Model='Second')])
        window.table.setSortingEnabled(False)
        window.table.setCurrentCell(1, 0)
        # Selection and checkboxes are synchronized by the existing table UI.
        # Establish a distinct command selection after choosing the current row.
        window.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
        window.table.item(1, 0).setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(window.table.item(0, 0).checkState(), Qt.CheckState.Checked)
        self.assertTrue(window.btn_device_card.isEnabled())
        with patch.object(self.gui, 'DeviceDialog', self.detail_stub):
            window.btn_device_card.click()
            card = window.device_dialogs[0]
            self.assertEqual(card.row['IP'], '192.0.2.2')
            self.assertEqual(window.table.item(0, 0).checkState(), Qt.CheckState.Checked)
            self.assertEqual(window.table.item(1, 0).checkState(), Qt.CheckState.Unchecked)
            self.assertIs(window.open_device_card(), card)
            self.assertEqual(len(window.device_dialogs), 1)
            card.close()
            self.assertEqual(window.device_dialogs, [])

    def test_explicit_context_row_wins_over_current_row_and_snapshot_is_frozen(self):
        window = self.window()
        original = dict(IP='192.0.2.10', Model='Original', Telemetry={'stale': False})
        with patch.object(self.gui, 'DeviceDialog', self.detail_stub):
            card = window.open_device_card(original)
            original['Telemetry']['stale'] = True
            self.assertFalse(card.row['Telemetry']['stale'])
            self.assertEqual(card.app_version, self.gui.CURRENT_VERSION)
            self.assertIs(card.access_profiles, window.access_profiles)
            self.assertEqual(card.ai_config, window.app_settings['ai'])

    def test_hidden_current_row_cannot_open_via_toolbar(self):
        window = self.window()
        window.on_result([dict(IP='192.0.2.1', Model='First')])
        window.table.setCurrentCell(0, 0)
        window.search_input.setText('nothing-matches')
        self.assertFalse(window.btn_device_card.isEnabled())
        with patch.object(self.gui, 'DeviceDialog') as constructor:
            self.assertIsNone(window.open_device_card())
            constructor.assert_not_called()

    def test_close_waits_for_device_read_and_then_closes_children(self):
        from PyQt6.QtGui import QCloseEvent
        window = self.window()
        with patch.object(self.gui, 'DeviceDialog', self.detail_stub):
            card = window.open_device_card(dict(IP='192.0.2.1'))
        card.active = True
        event = QCloseEvent()
        with patch.object(self.gui.QTimer, 'singleShot') as retry:
            window.closeEvent(event)
            retry.assert_called_once()
        self.assertFalse(event.isAccepted())
        self.assertTrue(card.stop_requested)
        self.assertIn(card, window.device_dialogs)
        self.assertIsNone(window.open_device_card(dict(IP='192.0.2.2')))
        card.active = False
        completed = QCloseEvent()
        window.closeEvent(completed)
        self.assertTrue(completed.isAccepted())
        self.assertEqual(window.device_dialogs, [])


if __name__ == '__main__':
    unittest.main()
