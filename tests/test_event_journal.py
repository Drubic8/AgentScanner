"""Offline journal coverage: event scope, bounded memory, safe text and preferences."""
import csv
import io
import json
import os
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from desktop_ui.event_log import LogEvent, MAX_MESSAGE, export_events
from desktop_ui.preferences import COLUMNS, defaults, normalize
from desktop_ui.device_filters import matches_device

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class JournalDataTests(unittest.TestCase):
    def test_credentials_removed_before_storage_and_all_exports(self):
        event = LogEvent.create('password="secret value" token=abc Authorization: Bearer xyz '
                                'Authorization: Basic YWJj stratum+tcp://name:private@pool.invalid')
        for payload in (event.message, export_events([event], "txt"), export_events([event], "csv"), export_events([event], "jsonl")):
            for secret in ("secret value", "abc", "xyz", "YWJj", "private"):
                self.assertNotIn(secret, payload)
            self.assertIn("[REDACTED]", payload)

    def test_export_preserves_full_date_and_csv_does_not_execute_formulas(self):
        event = LogEvent.create('=HYPERLINK("bad")', action="sleep")
        csv_rows = list(csv.DictReader(io.StringIO(export_events([event], "csv"))))
        self.assertTrue(csv_rows[0]["message"].startswith("'="))
        result = json.loads(export_events([event], "jsonl"))
        self.assertEqual(result["message"], event.message)
        self.assertIn("T", result["timestamp"])
        self.assertIn("sleep", export_events([event], "txt"))

    def test_command_context_outcomes_and_multiword_search(self):
        event = LogEvent.create("192.0.2.7: [unconfirmed] Device accepted; readback timed out", action="sleep")
        self.assertEqual((event.ip, event.level, event.outcome), ("192.0.2.7", "warning", "unconfirmed"))
        self.assertTrue(event.matches("sleep 192.0.2 accepted", outcome="unconfirmed"))
        self.assertFalse(event.matches("sleep missing"))
        self.assertFalse(event.matches(action="reboot"))
        self.assertLess(len(LogEvent.create("x" * 100000).message), MAX_MESSAGE + 50)

    def test_layout_migration_deduplicates_and_preserves_stable_column_ids(self):
        result = normalize({"column_order": ["Model", "Model", "removed", "LED"],
                            "column_widths": {"compact": {"IP": 130, "Real HR": 145, "Model": True, "LED": -1}},
                            "journal_language": "invalid"})
        self.assertEqual(result["column_order"][:3], ["IP", "Model", "LED"])
        self.assertEqual(set(result["column_order"]), set(COLUMNS))
        self.assertEqual(len(result["column_order"]), len(COLUMNS))
        self.assertEqual(result["column_widths"], {"compact": {"IP": 130, "Real HR": 145}})
        self.assertEqual(result["journal_language"], "ru")
        self.assertEqual(normalize(result), result)

    def test_combined_device_filters_distinguish_unknown_led_and_false(self):
        record = dict(IP="192.0.2.7", Model="L9", Firmware="VNish", Worker="pool.worker", Status="Sleep", IdentifyEnabled=False, Error="")
        self.assertTrue(matches_device(record, query="192.0.2 worker", model="L9", firmware="VNish", led="off", status="Sleep"))
        self.assertFalse(matches_device(record, led="unknown"))
        self.assertFalse(matches_device(record, errors=True))
        self.assertTrue(matches_device(dict(record, IdentifyEnabled=None, Error="HW ERR"), led="unknown", errors=True))


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in ("PyQt6", "pandas", "fpdf")), "Desktop dependencies not installed")
class JournalQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gemini_gui
        cls.gui = gemini_gui
        cls.app = gemini_gui.QApplication.instance() or gemini_gui.QApplication([])

    def journal(self):
        dialog = self.gui.LogDialog()
        self.addCleanup(dialog.close)
        return dialog

    def test_filters_export_scope_and_language_preserve_raw_diagnostics(self):
        dialog = self.journal()
        dialog.append_log("192.0.2.1: [succeeded] Verified", action="led_on")
        dialog.append_log("192.0.2.2: [failed] Device refused", action="sleep")
        dialog.flush()
        dialog.action.setCurrentIndex(dialog.action.findData("sleep"))
        self.assertEqual(dialog.proxy.rowCount(), 1)
        with TemporaryDirectory() as folder:
            output = Path(folder) / "filtered.jsonl"
            with patch("desktop_ui.log_dialog.QFileDialog.getSaveFileName", return_value=(str(output), "JSON Lines (*.jsonl)")):
                dialog.save_log()
            self.assertEqual([json.loads(line)["ip"] for line in output.read_text(encoding="utf-8").splitlines()], ["192.0.2.2"])
            dialog.scope.setCurrentIndex(dialog.scope.findData("all"))
            output = Path(folder) / "all.csv"
            with patch("desktop_ui.log_dialog.QFileDialog.getSaveFileName", return_value=(str(output), "CSV (*.csv)")):
                dialog.save_log()
            self.assertEqual(len(list(csv.DictReader(io.StringIO(output.read_text(encoding="utf-8-sig"))))), 2)
        dialog.language_box.setCurrentIndex(dialog.language_box.findData("en"))
        self.assertEqual(dialog.action.currentData(), "sleep")
        self.assertEqual(dialog.proxy.rowCount(), 1)
        self.assertEqual(dialog.proxy.index(0, 4).data(), "Failed")
        self.assertIn("Device refused", dialog.proxy.index(0, 5).data())
        dialog.search.setText("absent")
        dialog.scope.setCurrentIndex(dialog.scope.findData("visible"))
        self.assertFalse(dialog.export_button.isEnabled())
        dialog.scope.setCurrentIndex(dialog.scope.findData("all"))
        self.assertTrue(dialog.export_button.isEnabled())
        self.assertTrue(dialog.empty.isHidden() is False)

    def test_details_and_selected_copy_are_plain_text(self):
        dialog = self.journal()
        dialog.append_log("192.0.2.1: [failed] <b>raw API payload</b>\nline two", action="sleep")
        dialog.append_log("192.0.2.2: [succeeded] Other device", action="sleep")
        dialog.flush()
        dialog.table.selectRow(0)
        self.assertIn("<b>raw API payload</b>", dialog.detail.toPlainText())
        dialog.copy_events(selected=True)
        copied = self.app.clipboard().text()
        self.assertIn("line two", copied)
        self.assertNotIn("Other device", copied)
        dialog.follow.setChecked(False)
        dialog.append_log("Another event")
        dialog.flush()
        self.assertEqual(dialog.table.selectionModel().selectedRows()[0].row(), 0)

    def test_burst_retention_counts_and_clear_remove_pending_events(self):
        dialog = self.journal()
        dialog.model.limit = 25
        for index in range(100):
            dialog.append_log(f"192.0.2.1: [succeeded] event {index}", action="led_on")
        dialog.flush()
        self.assertEqual(dialog.model.rowCount(), 25)
        self.assertEqual(dialog.model.dropped, 75)
        self.assertTrue(dialog.model.events[0].message.endswith("event 75"))
        dialog.table.selectRow(0)
        dialog.append_log("new event")
        dialog.flush()
        self.assertEqual(dialog.model.dropped, 76)
        dialog.append_log("pending event")
        dialog.clear_log()
        self.app.processEvents()
        self.assertEqual(dialog.model.rowCount(), 0)
        self.assertFalse(dialog.pending)
        self.assertEqual(dialog.detail.toPlainText(), "")

    def test_manual_layout_roundtrip_and_capability_filter_do_not_select_hidden_devices(self):
        window = self.gui.GeminiApp(settings=defaults(), ranges=[])
        self.addCleanup(window.close)
        window.table_preset.setCurrentIndex(window.table_preset.findData("compact"))
        header = window.table.horizontalHeader()
        header.moveSection(header.visualIndex(list(COLUMNS).index("Model")), 1)
        window.table.setColumnWidth(1, 211)
        snapshot = normalize(window.app_settings)
        other = self.gui.GeminiApp(settings=snapshot, ranges=[])
        self.addCleanup(other.close)
        self.assertEqual(other.table.columnWidth(1), 211)
        self.assertEqual(other.table.horizontalHeader().visualIndex(1), 1)
        self.assertEqual(other.table.horizontalHeader().visualIndex(0), 0)
        self.assertFalse(other.table.isColumnHidden(list(COLUMNS).index("LED")))
        self.assertTrue(other.table.isColumnHidden(list(COLUMNS).index("Pool")))
        other.on_result([dict(IP="192.0.2.1", Model="L9", Status="Sleep", Firmware="VNish", Capabilities={"sleep": "supported"}),
                         dict(IP="192.0.2.2", Model="KS5", Status="Running", Firmware="Stock", Capabilities={"sleep": "unknown"})])
        other.btn_select_all.click()
        other.capability_filter.setCurrentIndex(other.capability_filter.findData("sleep"))
        selected = [other.table.item(row, 0).text() for row in range(other.table.rowCount())
                    if other.table.item(row, 0).checkState() == self.gui.Qt.CheckState.Checked]
        self.assertEqual(selected, ["192.0.2.1"])
        other.handle_worker_log("192.0.2.1: [unconfirmed] Accepted only", action="sleep")
        other.log_dialog.flush()
        self.assertEqual(other.log_dialog.model.events[-1].action, "sleep")

    def test_user_layout_is_written_to_disk_and_restored_on_next_start(self):
        from PyQt6.QtTest import QTest
        with TemporaryDirectory() as folder, patch.object(self.gui, "SETTINGS_FILE", Path(folder) / "app_settings.json"):
            window = self.gui.GeminiApp(ranges=[])
            try:
                window.table.setColumnWidth(1, 239)
                window.log_dialog.language_box.setCurrentIndex(window.log_dialog.language_box.findData("en"))
                QTest.qWait(500)
                saved = json.loads(self.gui.SETTINGS_FILE.read_text(encoding="utf-8"))
                self.assertEqual(saved["column_widths"][saved["density"]]["Model"], 239)
                self.assertEqual(saved["journal_language"], "en")
                second = self.gui.GeminiApp(ranges=[])
                try:
                    self.assertEqual(second.table.columnWidth(1), 239)
                    self.assertEqual(second.log_dialog.language, "en")
                finally:
                    second.close()
            finally:
                window.close()


if __name__ == "__main__":
    unittest.main()
