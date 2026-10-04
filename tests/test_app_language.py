"""Catalog integrity and real interface switching with existing device state."""
import ast
import importlib.util
import os
from pathlib import Path
from string import Formatter
import unittest
from unittest.mock import patch

from desktop_ui.i18n import CATALOG, get_language, set_language, tr, tr_error
from desktop_ui.preferences import normalize

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]


class LanguageCatalogTests(unittest.TestCase):
    def tearDown(self):
        set_language("ru")

    def test_legacy_language_migrates_and_invalid_values_are_rejected(self):
        self.assertEqual(normalize({"journal_language": "en"})["language"], "en")
        self.assertEqual(normalize({"language": "ru", "journal_language": "en"})["journal_language"], "ru")
        self.assertEqual(normalize({"language": "unknown"})["language"], "ru")

    def test_catalog_preserves_fields_and_covers_desktop_call_sites(self):
        for source, target in CATALOG.items():
            source_fields = [(field, spec, conv) for _, field, spec, conv in Formatter().parse(source) if field]
            target_fields = [(field, spec, conv) for _, field, spec, conv in Formatter().parse(target) if field]
            self.assertCountEqual(source_fields, target_fields, source)
        for path in [ROOT / "gemini_gui.py", *(ROOT / "desktop_ui").glob("*.py")]:
            module = ast.parse(path.read_text(encoding="utf-8"), feature_version=(3, 11))
            for node in ast.walk(module):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "tr" and node.args:
                    source = node.args[0]
                    if isinstance(source, ast.Constant) and isinstance(source.value, str):
                        self.assertIn(source.value, CATALOG, f"{path.name}:{node.lineno}")

    def test_interpolated_user_text_is_not_reinterpreted_as_translation_keys(self):
        set_language("en")
        name = "Площадка {p1} $secret"
        self.assertEqual(tr("Удалить {p0}", p0=name), "Delete " + name)
        self.assertEqual(tr_error("В одной группе допускается не более 4096 IP-адресов."),
                         "Each group supports up to 4096 IP addresses.")
        self.assertEqual(tr_error("FAN ERR (B1)"), "FAN ERR (B1)")


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in ("PyQt6", "pandas", "fpdf")),
                     "Desktop dependencies not installed")
class ApplicationLanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gemini_gui
        cls.gui = gemini_gui
        cls.app = gemini_gui.QApplication.instance() or gemini_gui.QApplication([])

    def tearDown(self):
        set_language("ru")

    def test_live_switch_preserves_rows_stale_records_filters_selection_and_journal(self):
        from PyQt6.QtCore import Qt
        from miner_scanner.access import AccessProfiles
        with patch.object(self.gui.AccessStore, "load", return_value=AccessProfiles([])):
            window = self.gui.GeminiApp(settings={"theme": "dark"}, ranges=[
                {"name": "Мои устройства", "ranges": ["192.0.2.1-2"], "enabled": True}])
        self.addCleanup(window.close)
        window.on_result([dict(IP="192.0.2.1", Model="Antminer T21", Status="Sleep", Firmware="Stock"),
                          dict(IP="192.0.2.2", Model="Antminer S21", Status="Running", Firmware="Stock")])
        window.on_result([dict(IP="192.0.2.2", Model="Antminer S21", Status="Unknown", Firmware="Stock", Stale=True)])
        window.search_input.setText("T21")
        window.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
        window.add_log("192.0.2.1: [succeeded] device API message", action="sleep")
        window.log_dialog.flush()
        events = window.log_dialog.model.events.copy()
        window.change_application_language("en")
        self.assertEqual(get_language(), "en")
        self.assertEqual(window.btn_scan.text(), "Start scan")
        self.assertEqual(window.table.rowCount(), 2)
        self.assertEqual(len(window.scan_data), 2)
        self.assertEqual(window.search_input.text(), "T21")
        self.assertTrue(window.table.isRowHidden(1))
        self.assertEqual(window.table.item(0, 3).text(), "Sleep")
        self.assertEqual(window.table.item(0, 0).checkState(), Qt.CheckState.Checked)
        self.assertTrue(window.table.item(0, 0).isSelected())
        self.assertEqual(window.ranges_config[0]["name"], "Мои устройства")
        self.assertEqual(window.log_dialog.model.events, events)
        from desktop_ui.settings_dialog import SettingsDialog
        settings = SettingsDialog(window.app_settings, window)
        self.assertEqual(settings.navigation.item(0).text(), "General")
        settings.close()
        window.change_application_language("ru")
        self.assertEqual(window.btn_scan.text(), "Начать сканирование")
        self.assertEqual(window.table.item(0, 3).text(), "Сон")
        self.assertEqual(window.table.item(0, 0).checkState(), Qt.CheckState.Checked)
