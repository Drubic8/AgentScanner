"""Long worker names remain readable and widths survive a settings round trip."""
import importlib.util
import os
import unittest
from unittest.mock import patch

from desktop_ui.preferences import COLUMNS, defaults, normalize

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


@unittest.skipUnless(importlib.util.find_spec('PyQt6') and importlib.util.find_spec('pandas'), 'Desktop dependencies not installed')
class ColumnSizingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gemini_gui
        cls.gui = gemini_gui
        cls.app = gemini_gui.QApplication.instance() or gemini_gui.QApplication([])

    def window(self):
        window = self.gui.GeminiApp(settings=defaults(), ranges=[])
        self.addCleanup(window.close)
        return window

    def test_long_worker_fits_after_scan_and_survives_reload(self):
        window = self.window()
        worker = 'owner.' + 'long-worker-name-' * 24
        window.on_result([{'IP': '192.0.2.1', 'SortIP': 1, 'Model': 'Demo', 'Worker': worker}])
        window.on_finished()
        index = list(COLUMNS).index('Worker')
        width = window.table.columnWidth(index)
        self.assertGreater(width, 1200)
        self.assertGreaterEqual(width, window.table.fontMetrics().horizontalAdvance(worker))
        self.assertEqual(window.table.item(0, index).toolTip(), worker)
        restored = self.gui.GeminiApp(settings=normalize(window.app_settings), ranges=[])
        self.addCleanup(restored.close)
        self.assertEqual(restored.table.columnWidth(index), width)
        self.assertEqual(window.table.columnWidth(list(COLUMNS).index('LED')), 46)

    def test_manual_width_is_preserved_when_automatic_fit_is_disabled(self):
        window = self.window()
        window.set_auto_fit_columns(False)
        index = list(COLUMNS).index('Worker')
        window.table.setColumnWidth(index, 210)
        window.on_result([{'IP': '192.0.2.1', 'Model': 'Demo', 'Worker': 'very-long-name-' * 20}])
        window.on_finished()
        self.assertEqual(window.table.columnWidth(index), 210)
        window.table.horizontalHeader().sectionHandleDoubleClicked.emit(index)
        self.assertGreater(window.table.columnWidth(index), 210)
        self.assertFalse(normalize(window.app_settings)['auto_fit_columns'])

    def test_column_autofit_is_not_repeated_for_each_scan_result(self):
        window = self.window()
        with patch.object(window, 'fit_table_columns') as fit:
            for i in range(10):
                window.on_result([{'IP': f'192.0.2.{i+1}', 'Model': 'Demo'}])
            fit.assert_not_called()
            window.on_finished()
            fit.assert_called_once_with()
