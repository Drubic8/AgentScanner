"""Network selection and editing checks; no ASIC or production settings access."""
import importlib.util
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from desktop_ui.network_groups import (normalize_groups, preview_ranges, selected_ranges,
    validate_name, folder_state, move_nodes, node_at, walk_networks)
from desktop_ui.preferences import defaults, load_json

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class NetworkGroupsTests(unittest.TestCase):
    def tree(self):
        return normalize_groups([{'type': 'folder', 'name': 'Site', 'children': [
            {'name': 'Row 1', 'ranges': ['192.0.2.1'], 'enabled': True},
            {'type': 'folder', 'name': 'Sleep', 'children': [
                {'name': 'Row 2', 'ranges': ['192.0.2.2'], 'enabled': False}]}]},
            {'name': 'Spare', 'ranges': ['192.0.2.3'], 'enabled': True}])

    def test_nested_settings_roundtrip_and_only_enabled_leaves_are_targets(self):
        import json
        groups = self.tree()
        self.assertEqual(normalize_groups(json.loads(json.dumps(groups))), groups)
        self.assertEqual(selected_ranges(groups), ['192.0.2.1', '192.0.2.3'])
        self.assertEqual(folder_state(groups[0]), 'mixed')
        self.assertEqual(folder_state({'type': 'folder', 'children': []}), 'off')

    def test_move_selection_roots_preserves_networks_and_flags(self):
        groups = self.tree()
        moved = move_nodes(groups, [(0, 1), (0, 1, 0)], ())
        self.assertEqual(len(list(walk_networks(moved))), 3)
        self.assertEqual(selected_ranges(moved), selected_ranges(groups))
        self.assertEqual(moved[2]['name'], 'Sleep')
        self.assertFalse(moved[2]['children'][0]['enabled'])
        self.assertEqual(len(groups[0]['children']), 2)
        # Destination is retained even when removing an earlier sibling shifts its path.
        moved = move_nodes(groups, [(0, 0)], (0, 1))
        self.assertEqual(moved[0]['children'][0]['children'][1]['name'], 'Row 1')

    def test_moves_reject_cycles_and_duplicate_names_before_mutation(self):
        groups = self.tree()
        with self.assertRaises(ValueError):
            move_nodes(groups, [(0,)], (0, 1))
        groups.append({'name': 'Row 1', 'ranges': ['192.0.2.10']})
        with self.assertRaisesRegex(ValueError, 'уже есть'):
            move_nodes(groups, [(0, 0)], ())
        self.assertEqual(groups[0]['children'][0]['name'], 'Row 1')
    def test_legacy_migration_preserves_ranges_and_explicit_disabled_state(self):
        self.assertEqual(normalize_groups({"Old": "192.0.2.1,192.0.2.2"}), [
            {"name": "Old", "ranges": ["192.0.2.1", "192.0.2.2"], "enabled": True}])
        groups = normalize_groups([{"name": "Old", "range": "192.0.2.1"},
                                   {"name": "Off", "ranges": ["192.0.2.2"], "enabled": False}])
        self.assertEqual(selected_ranges(groups), ["192.0.2.1"])
        groups[0]["enabled"] = False
        self.assertEqual(selected_ranges(groups), [])

    def test_preview_handles_paste_overlap_and_cidr_host_rules(self):
        preview = preview_ranges("192.0.2.0/30\n192.0.2.1-3; 192.0.2.3, 192.0.2.4/32")
        self.assertEqual(preview.addresses, [f"192.0.2.{i}" for i in range(1, 5)])
        self.assertEqual(preview.repeated, 3)
        self.assertEqual(len(preview.ranges), 4)
        self.assertEqual(preview_ranges("192.0.2.4/31").addresses, ["192.0.2.4", "192.0.2.5"])

    def test_invalid_line_and_oversized_combination_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Строка 2"):
            preview_ranges("192.0.2.1\n192.0.2.8-2")
        for value in ("", "::1", "0.0.0.0/0", "10.0.0.0/20\n10.0.16.1-3"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                preview_ranges(value)
        with self.assertRaisesRegex(ValueError, "уже есть"):
            validate_name("  площадка А ", ["Площадка а"])


@unittest.skipUnless(importlib.util.find_spec("PyQt6") and importlib.util.find_spec("pandas") and importlib.util.find_spec("fpdf"), "Desktop dependencies not installed")
class NetworkUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gemini_gui
        cls.gui = gemini_gui
        cls.app = gemini_gui.QApplication.instance() or gemini_gui.QApplication([])

    def window(self):
        window = self.gui.GeminiApp(settings=defaults(), ranges=[
            {"name": "Alpha", "ranges": ["192.0.2.1-2"], "enabled": True},
            {"name": "Beta", "ranges": ["198.51.100.1"], "enabled": False}])
        self.addCleanup(window.close)
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "ip_ranges.json"
        patcher = patch.object(self.gui, "CONFIG_FILE", path)
        patcher.start(); self.addCleanup(patcher.stop)
        return window, path

    def test_editor_preview_validation_and_cancel(self):
        from desktop_ui.range_dialog import IPRangeDialog
        dialog = IPRangeDialog(existing_names=["Existing"])
        self.addCleanup(dialog.close)
        self.assertFalse(dialog.btn_save.isEnabled())
        dialog.le_name.setText("Existing")
        dialog.te_ranges.setPlainText("192.0.2.1-3\n192.0.2.2")
        self.assertFalse(dialog.update_preview())
        self.assertIn("уже есть", dialog.error.text())
        dialog.le_name.setText("New")
        self.assertTrue(dialog.update_preview())
        self.assertIn("3 IP", dialog.summary.text())
        dialog.te_ranges.setPlainText("192.0.2.1\nwrong")
        dialog.validate_and_accept()
        self.assertEqual(dialog.result(), dialog.DialogCode.Rejected)
        self.assertIn("Строка 2", dialog.error.text())
        dialog.te_ranges.setPlainText("192.0.2.1;192.0.2.2")
        dialog.validate_and_accept()
        self.assertEqual(dialog.get_data(), ("New", ["192.0.2.1", "192.0.2.2"]))
        self.assertEqual(dialog.result(), dialog.DialogCode.Accepted)

    def test_folder_checkbox_filter_bulk_and_failed_save(self):
        window, path = self.window()
        groups = NetworkGroupsTests().tree()
        window.commit_ranges(groups)
        panel = window.ranges_panel
        qt = self.gui.Qt
        folder = panel.items[(0,)]
        self.assertEqual(folder.checkState(0), qt.CheckState.PartiallyChecked)
        folder.setCheckState(0, qt.CheckState.Checked)
        self.assertEqual(selected_ranges(load_json(path)), ['192.0.2.1', '192.0.2.2', '192.0.2.3'])
        panel.list_ranges.setCurrentItem(panel.items[(0,)])
        panel.search.setText('Row 1')
        self.assertTrue(panel.items[(0, 1)].isHidden())
        panel.disable_selected.click()
        saved = load_json(path)
        self.assertFalse(node_at(saved, (0, 0))['enabled'])
        self.assertTrue(node_at(saved, (0, 1, 0))['enabled'])
        panel.search.clear()
        with patch.object(self.gui, 'write_json', side_effect=OSError('disk full')), patch.object(self.gui.QMessageBox, 'critical'):
            panel.items[(0,)].setCheckState(0, qt.CheckState.Unchecked)
        self.assertEqual(selected_ranges(window.ranges_config), selected_ranges(saved))
        self.assertEqual(panel.items[(0,)].checkState(0), qt.CheckState.PartiallyChecked)

    def test_nested_add_rename_delete_and_reload(self):
        from desktop_ui.folder_dialog import FolderDialog
        window, path = self.window()
        window.commit_ranges(NetworkGroupsTests().tree(), (0, 1))
        with patch.object(FolderDialog, 'exec', return_value=1), patch.object(FolderDialog, 'get_data', return_value=('Night shift', (0,))):
            window.edit_subnet((0, 1))
        self.assertEqual(node_at(load_json(path), (0, 1))['name'], 'Night shift')
        with patch.object(FolderDialog, 'exec', return_value=1), patch.object(FolderDialog, 'get_data', return_value=('Backup', (0, 1))):
            window.add_folder_dialog()
        self.assertEqual(node_at(load_json(path), (0, 1, 1))['type'], 'folder')
        with patch.object(self.gui.QMessageBox, 'question', return_value=self.gui.QMessageBox.StandardButton.Yes):
            window.delete_range((0, 1))
        self.assertEqual(selected_ranges(load_json(path)), ['192.0.2.1', '192.0.2.3'])
        self.assertEqual(normalize_groups(load_json(path)), window.ranges_config)

    def test_folder_dialog_allows_root_creation_and_reports_sibling_conflicts(self):
        from desktop_ui.folder_dialog import FolderDialog
        dialog = FolderDialog(NetworkGroupsTests().tree(), (0,))
        self.addCleanup(dialog.close)
        dialog.name.setText('Row 1')
        dialog.save()
        self.assertIn('уже есть', dialog.error.text())
        self.assertEqual(dialog.result(), dialog.DialogCode.Rejected)
        dialog.location.setCurrentIndex(0)
        dialog.name.setText('Another site')
        dialog.save()
        self.assertEqual(dialog.get_data(), ('Another site', ()))
        self.assertEqual(dialog.result(), dialog.DialogCode.Accepted)

    def test_checkboxes_persist_selection_independently_of_focused_and_hidden_rows(self):
        window, path = self.window()
        panel = window.ranges_panel
        panel.list_ranges.setCurrentRow(1)
        self.assertEqual(selected_ranges(window.ranges_config), ["192.0.2.1-2"])
        panel.search.setText("Beta")
        self.assertTrue(panel.list_ranges.item(0).isHidden())
        panel.list_ranges.item(1).setCheckState(0, self.gui.Qt.CheckState.Checked)
        self.assertEqual(len(selected_ranges(window.ranges_config)), 2)
        self.assertTrue(all(group["enabled"] for group in load_json(path)))
        panel.select_all.click()
        self.assertEqual(selected_ranges(window.ranges_config), [])
        with patch.object(self.gui, "ScanWorker") as worker, patch.object(self.gui.QMessageBox, "warning"):
            window.start_scan()
            worker.assert_not_called()
        restored = normalize_groups(load_json(path))
        self.assertTrue(all(not group["enabled"] for group in restored))
        panel.select_all.click()
        self.assertTrue(all(group["enabled"] for group in window.ranges_config))

    def test_write_failure_rolls_back_checkbox_and_preserves_previous_config(self):
        window, _ = self.window()
        with patch.object(self.gui, "write_json", side_effect=OSError("disk full")), patch.object(self.gui.QMessageBox, "critical") as critical:
            window.list_ranges.item(0).setCheckState(0, self.gui.Qt.CheckState.Unchecked)
        critical.assert_called_once()
        self.assertTrue(window.ranges_config[0]["enabled"])
        self.assertEqual(window.list_ranges.item(0).checkState(0), self.gui.Qt.CheckState.Checked)

    def test_shift_ctrl_selection_bulk_toggle_and_filtered_scope(self):
        from PyQt6.QtTest import QTest
        window, path = self.window()
        window.commit_ranges([{"name": f"Site {i}", "ranges": [f"192.0.2.{i+1}"], "enabled": False}
                              for i in range(4)])
        window.show()
        self.app.processEvents()
        panel = window.ranges_panel
        listing = panel.list_ranges
        qt = self.gui.Qt
        def click(row, modifier=qt.KeyboardModifier.NoModifier):
            QTest.mouseClick(listing.viewport(), qt.MouseButton.LeftButton, modifier,
                             listing.visualItemRect(listing.item(row)).center())
        click(0)
        click(2, qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(len(listing.selectedItems()), 3)
        click(1, qt.KeyboardModifier.ControlModifier)
        self.assertEqual(len(listing.selectedItems()), 2)
        panel.enable_selected.click()
        self.assertEqual([g['enabled'] for g in load_json(path)], [True, False, True, False])
        self.assertEqual(len(listing.selectedItems()), 2)
        panel.search.setText("Site 0")
        panel.disable_selected.click()
        self.assertEqual([g['enabled'] for g in load_json(path)], [False, False, True, False])

    def test_delete_cancel_and_failure_never_change_saved_networks(self):
        window, _ = self.window()
        with patch.object(self.gui.QMessageBox, "question", return_value=self.gui.QMessageBox.StandardButton.No):
            window.delete_range(0)
        self.assertEqual(len(window.ranges_config), 2)
        with patch.object(self.gui.QMessageBox, "question", return_value=self.gui.QMessageBox.StandardButton.Yes), patch.object(self.gui, "write_json", side_effect=OSError("disk full")), patch.object(self.gui.QMessageBox, "critical"):
            window.delete_range(0)
        self.assertEqual(len(window.ranges_config), 2)
        with patch.object(self.gui.QMessageBox, "question", return_value=self.gui.QMessageBox.StandardButton.Yes):
            window.delete_range(0)
        self.assertEqual([group["name"] for group in window.ranges_config], ["Beta"])
