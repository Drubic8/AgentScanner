"""Offline checks for totals disclosure, settings navigation and nested access editing."""
import os
import importlib.util
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in ("PyQt6", "pandas", "fpdf")), "Desktop dependencies not installed")
class CompactDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gemini_gui
        cls.gui = gemini_gui
        cls.app = gemini_gui.QApplication.instance() or gemini_gui.QApplication([])

    def window(self, settings=None):
        from miner_scanner.access import AccessProfiles, standard_profiles
        with patch.object(self.gui.AccessStore, "load", return_value=AccessProfiles(standard_profiles())):
            window = self.gui.GeminiApp(settings=settings or {}, ranges=[])
        self.addCleanup(window.close)
        window.resize(1060, 680)
        window.show()
        self.app.processEvents()
        return window

    def test_collapse_gives_table_more_space_and_preserves_all_models_and_rate_groups(self):
        window = self.window()
        rows = [dict(IP=f"192.0.2.{index + 1}", Model=f"Model-{index}", Status="Running",
                     Algo=("SHA-256", "Scrypt", "Equihash")[index % 3],
                     Real=("200.00 TH/s", "16.00 GH/s", "140.00 kSol/s")[index % 3]) for index in range(30)]
        window.on_result(rows)
        window.stats_timer.stop()
        window.update_stats()
        self.app.processEvents()
        panel = window.summary_panel
        self.assertFalse(panel.toggle.isChecked())
        self.assertLess(panel.height(), 100)
        self.assertEqual(len(panel.detail_texts[1].toPlainText().splitlines()), 30)
        self.assertIn("Model-29", panel.detail_texts[1].toPlainText())
        self.assertEqual(len(panel.detail_texts[2].toPlainText().splitlines()), 3)
        for unit in ("TH/s", "GH/s", "kSol/s"):
            self.assertIn(unit, panel.detail_texts[2].toPlainText())
        height = window.table.height()
        panel.toggle.click()
        self.app.processEvents()
        self.assertTrue(panel.details.isVisible())
        self.assertTrue(window.app_settings["summary_expanded"])
        self.assertGreaterEqual(height - window.table.height(), 140)
        panel.toggle.click()
        self.app.processEvents()
        self.assertEqual(window.table.height(), height)
        self.assertIn("Model-29", panel.detail_texts[1].toPlainText())

    def test_restored_expansion_and_theme_button_remain_accessible(self):
        window = self.window({"summary_expanded": True, "theme": "dark"})
        self.assertTrue(window.summary_panel.details.isVisible())
        self.assertFalse(window.btn_theme.icon().isNull())
        self.assertLess(window.btn_theme.width(), 50)
        self.assertEqual(window.btn_theme.text(), "")
        before = window.btn_theme.accessibleName()
        window.btn_theme.click()
        self.assertFalse(window.dark_mode)
        self.assertEqual(window.app_settings["theme"], "light")
        self.assertNotEqual(window.btn_theme.accessibleName(), before)
        self.assertIn("Ctrl+Shift+T", window.btn_theme.toolTip())

    def test_access_page_opens_editor_under_settings_with_selected_device_scope(self):
        window = self.window()
        window.on_result([dict(IP="192.0.2.7", Model="L9", DeviceId="demo-device")])
        window.table.item(0, 0).setCheckState(self.gui.Qt.CheckState.Checked)
        dialog = self.gui.SettingsDialog(window.app_settings, window, configure_access=window.configure_device_access)
        self.addCleanup(dialog.close)
        access_page = next(i for i in range(dialog.navigation.count()) if dialog.navigation.item(i).text() == "Доступ к ASIC")
        dialog.navigation.setCurrentRow(access_page)
        fake = Mock()
        fake.exec.return_value = 0
        with patch.object(self.gui, "AccessProfilesDialog", return_value=fake) as editor:
            dialog.access_button.click()
        self.assertEqual(editor.call_args.kwargs["target_ips"], ["192.0.2.7"])
        self.assertIs(editor.call_args.kwargs["parent"], dialog)
        fake.exec.assert_called_once()
        self.assertEqual(dialog.result(), dialog.DialogCode.Rejected)
        sidebar = window.findChild(self.gui.QWidget, "Sidebar")
        for button in sidebar.findChildren(self.gui.QPushButton):
            self.assertNotIn(button.text(), ("Настройки программы", "Доступ к ASIC"))


if __name__ == "__main__":
    unittest.main()
