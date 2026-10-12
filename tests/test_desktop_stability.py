"""No network: ordering contract, table refresh, and subprocess crash diagnostics."""
import os
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in ('PyQt6', 'pandas', 'fpdf')), 'Desktop dependencies not installed')
class DesktopStabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_numeric_order_is_transitive_with_invalid_and_nonfinite_readings(self):
        from desktop_ui.table_items import SmartSortItem
        values = ['2', '10', '1a', '', '-', 'nan', 'inf', '-inf', '0.00 TH/s', '20 30']
        items = [SmartSortItem(value) for value in values]
        for a in items:
            self.assertFalse(a < a)
            for b in items:
                for c in items:
                    if a < b and b < c:
                        self.assertTrue(a < c, (a.text(), b.text(), c.text()))
        self.assertEqual([item.text() for item in sorted(items)][:4],
                         ['0.00 TH/s', '2', '10', '20 30'])

    def test_refresh_sorted_selected_rows_and_restore_signals_after_failure(self):
        from gemini_gui import GeminiApp, Qt
        window = GeminiApp(settings={'check_updates': False}, ranges=[])
        try:
            rows = [{'IP': f'192.0.2.{i}', 'SortIP': i, 'Real': str(i), 'Status': 'Running'}
                    for i in range(1, 101)]
            window.on_result(rows)
            for cycle in range(6):
                window.table.sortItems(6, Qt.SortOrder.DescendingOrder)
                window.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
                window.on_result([dict(row, Real=str(101 - row['SortIP'])) for row in rows])
                self.app.processEvents()
                self.assertEqual(window.table.rowCount(), 100)
                self.assertEqual(len({window.table.item(i, 0).text() for i in range(100)}), 100)
            with self.assertRaises(TypeError):
                window.on_result([None])
            self.assertTrue(window.table.isSortingEnabled())
            self.assertFalse(window.table.signalsBlocked())
            self.assertFalse(window.table.selectionModel().signalsBlocked())
            window.on_result(rows)
            self.assertEqual(window.table.rowCount(), 100)
        finally:
            window.close()

    def test_exception_report_has_stack_but_not_exception_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            code = ('from desktop_ui.crash_diagnostics import enable\n'
                    f'enable({directory!r}, "test")\n'
                    'raise RuntimeError("private-device-response")\n')
            result = subprocess.run([sys.executable, '-c', code], capture_output=True,
                                    timeout=15)
            self.assertNotEqual(result.returncode, 0)
            report = next(Path(directory).glob('crash-*.log')).read_text(encoding='utf-8')
            self.assertIn('Unhandled exception: RuntimeError', report)
            self.assertIn('line 3', report)
            self.assertNotIn('private-device-response', report)

    def test_subnet_checkbox_keeps_native_item_alive_and_keeps_rapid_edits(self):
        from PyQt6 import sip
        from PyQt6.QtCore import Qt
        from desktop_ui.ranges_panel import RangesPanel
        panel = RangesPanel()
        panel.set_groups([{'type': 'folder', 'name': 'Test', 'children': [
            {'name': 'A', 'ranges': ['192.0.2.1'], 'enabled': False},
            {'name': 'B', 'ranges': ['192.0.2.2'], 'enabled': False},
        ]}])
        commits = []
        def save_and_refresh(groups):
            commits.append(groups)
            panel.set_groups(groups)
        panel.changed.connect(save_and_refresh)
        try:
            for _ in range(20):
                a, b = panel.items[(0, 0)], panel.items[(0, 1)]
                state = Qt.CheckState.Unchecked if a.checkState(0) == Qt.CheckState.Checked else Qt.CheckState.Checked
                a.setCheckState(0, state)
                self.assertFalse(sip.isdeleted(a))
                b.setCheckState(0, state)
                self.assertFalse(sip.isdeleted(b))
                self.assertEqual(panel.groups[0]['children'][0]['enabled'], state == Qt.CheckState.Checked)
                self.assertEqual(panel.groups[0]['children'][1]['enabled'], state == Qt.CheckState.Checked)
                self.app.processEvents()
                self.assertTrue(sip.isdeleted(a))
                self.assertEqual(panel.items[(0,)].checkState(0), state)
            self.assertEqual(len(commits), 40)
        finally:
            panel.close()

