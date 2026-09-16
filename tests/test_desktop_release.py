"""Preference migration, filtering, Qt worker lifecycle and release consistency."""
import importlib.util
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from desktop_ui.preferences import defaults, load_json, normalize, write_json
from scripts.build_windows import release_version

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class PreferenceTests(unittest.TestCase):
    def test_partial_legacy_settings_get_defaults_and_safe_limits(self):
        result = normalize({"timeout": "bad", "workers": 99999, "theme": None, "ui_cols": ["Model"], "pdf_cols": [], "copy_csv": "false"})
        self.assertEqual(result["timeout"], 2)
        self.assertEqual(result["workers"], 128)
        self.assertEqual(result["theme"], "system")
        self.assertEqual(result["ui_cols"], ["IP", "Model"])
        self.assertTrue(result["pdf_cols"])
        self.assertFalse(result["copy_csv"])

    def test_legacy_fallback_and_atomic_preference_roundtrip(self):
        path = Path(__file__).parent / f"prefs-{uuid4().hex}.json"
        legacy = path.with_suffix(".legacy.json")
        for file in (path, legacy, path.with_suffix(".json.tmp")):
            self.addCleanup(lambda file=file: file.unlink(missing_ok=True))
        write_json(legacy, {"theme": "dark"})
        self.assertEqual(load_json(path, legacy)["theme"], "dark")
        write_json(path, {"theme": "light", "name": "Сеть"})
        self.assertEqual(load_json(path, legacy)["theme"], "light")
        self.assertEqual(load_json(legacy)["theme"], "dark")
        path.write_text("broken", encoding="utf-8")
        self.assertEqual(load_json(path, legacy, {}), {})

    def test_release_versions_match(self):
        version, parts = release_version()
        self.assertEqual(version, "2.0.1")
        self.assertEqual(parts, (2, 0, 1))


@unittest.skipUnless(importlib.util.find_spec("PyQt6") and importlib.util.find_spec("pandas") and importlib.util.find_spec("fpdf"), "Desktop dependencies not installed")
class DesktopReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gemini_gui
        cls.gui = gemini_gui
        cls.app = gemini_gui.QApplication.instance() or gemini_gui.QApplication([])

    def window(self):
        window = self.gui.GeminiApp(settings=defaults(), ranges=[])
        self.addCleanup(window.close)
        return window

    def test_settings_cancel_preserves_original_and_save_keeps_column_codes(self):
        original = defaults()
        dialog = self.gui.SettingsDialog(original)
        dialog.theme.setCurrentIndex(dialog.theme.findData("dark"))
        dialog.reject()
        self.assertEqual(original["theme"], "system")
        dialog = self.gui.SettingsDialog(original)
        dialog.theme.setCurrentIndex(dialog.theme.findData("dark"))
        dialog.workers.setValue(16)
        dialog.list_pdf_cols.item(1).setCheckState(self.gui.Qt.CheckState.Unchecked)
        dialog.save_and_close()
        self.assertEqual(dialog.result(), dialog.DialogCode.Accepted)
        self.assertEqual(dialog.settings["theme"], "dark")
        self.assertEqual(dialog.settings["workers"], 16)
        self.assertNotIn("Model", dialog.settings["pdf_cols"])
        self.assertIn("Real HR", dialog.settings["pdf_cols"])
        self.assertEqual(original, defaults())

    def test_settings_reject_empty_export_and_no_manufacturers(self):
        dialog = self.gui.SettingsDialog(defaults())
        for box in dialog.filters.values():
            box.setChecked(False)
        with patch.object(self.gui.QMessageBox, "warning") as warning:
            dialog.save_and_close()
            warning.assert_called_once()
        self.assertEqual(dialog.result(), dialog.DialogCode.Rejected)
        dialog.filters["scan_other"].setChecked(True)
        for index in range(dialog.list_pdf_cols.count()):
            dialog.list_pdf_cols.item(index).setCheckState(self.gui.Qt.CheckState.Unchecked)
        with patch.object(self.gui.QMessageBox, "warning") as warning:
            dialog.save_and_close()
            warning.assert_called_once()
        self.assertEqual(dialog.result(), dialog.DialogCode.Rejected)

    def test_filters_unselect_hidden_devices_and_theme_preserves_error_color(self):
        window = self.window()
        rows = [dict(IP="192.0.2.1", Model="S21", Status="Running", Real="200 TH/s", Firmware="Stock", DeviceId="one"),
                dict(IP="192.0.2.2", Model="M60", Status="Error", Real="—", Firmware="Stock", DeviceId="two")]
        window.on_result(rows)
        window.btn_select_all.click()
        self.assertIn("2", window.lbl_selected_count.text())
        window.search_input.setText("S21")
        selected = [window.table.item(i, 0).text() for i in range(window.table.rowCount())
                    if window.table.item(i, 0).checkState() == self.gui.Qt.CheckState.Checked]
        self.assertEqual(selected, ["192.0.2.1"])
        window.search_input.clear()
        window.dark_mode = True
        window.apply_theme()
        error_index = next(i for i in range(window.table.rowCount()) if window.table.item(i, 0).text() == "192.0.2.2")
        self.assertEqual(window.table.item(error_index, 3).foreground().color().name(), "#ff919b")
        window.search_input.setText("no-such-device")
        self.assertEqual(window.table_stack.currentIndex(), 1)
        self.assertFalse(window.btn_remote.isEnabled())

    def test_scan_button_runs_worker_and_returns_to_ready_without_network(self):
        from miner_scanner.service import ScannerService
        from miner_scanner.repository import DeviceRepository
        from tests.fakes import FakeFactory
        repository = DeviceRepository(":memory:")
        self.addCleanup(repository.close)
        service = ScannerService(repository=repository, transport_factory=FakeFactory())
        window = self.window()
        window.ranges_config = [{"name": "test", "ranges": ["192.0.2.1-2"]}]
        window.app_settings["workers"] = 2
        with patch("miner_scanner.core.default_service", return_value=service), patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected network request")):
            window.start_scan()
            deadline = time.monotonic() + 5
            while window.worker.isRunning() and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.005)
            window.worker.stop()
            self.assertTrue(window.worker.wait(3000))
            self.app.processEvents()
        self.assertEqual(window.worker.options.workers, 2)
        self.assertEqual(window.table.rowCount(), 2)
        self.assertTrue(window.btn_scan.isEnabled())
        self.assertFalse(window.btn_stop.isEnabled())

    def test_report_sort_works_when_sort_column_is_not_exported(self):
        from desktop_ui.reports import export_frame
        rows = [{"IP": "192.0.2.10", "Model": "ten", "RawHash": 2}, {"IP": "192.0.2.2", "Model": "two", "RawHash": 100}]
        self.assertEqual(export_frame(rows, ["Model"], "IP")["Model"].tolist(), ["two", "ten"])
        self.assertEqual(export_frame(rows, ["IP"], "Real HR")["IP"].tolist(), ["192.0.2.10", "192.0.2.2"])

    def test_update_versions_are_numeric_and_sources_validated(self):
        from desktop_ui.updates import version_tuple, validate_manifest
        self.assertGreater(version_tuple("2.10.0"), version_tuple("2.9.0"))
        with self.assertRaises(ValueError):
            validate_manifest({"version": "2.1.0", "url": "http://example.invalid/program.exe"})
