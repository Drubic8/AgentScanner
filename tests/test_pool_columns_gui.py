"""Offline desktop checks for configured pools, saved views and PDF projection."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from desktop_ui.preferences import COLUMNS, POOL_COLUMNS, TABLE_PRESETS, defaults, normalize

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
DESKTOP_AVAILABLE = all(importlib.util.find_spec(name) for name in ('PyQt6', 'pandas', 'fpdf'))


class PoolPreferenceTests(unittest.TestCase):
    def test_existing_custom_layout_and_export_selections_are_preserved(self):
        saved = dict(ui_cols=['IP', 'Worker'], csv_cols=['IP', 'Worker'],
                     pdf_cols=['IP', 'Pool'], table_preset='custom', columns_version=2,
                     column_order=['IP', 'Worker', 'LED'],
                     column_widths={'compact': {'Worker': 540}})
        result = normalize(saved)
        self.assertEqual(result['ui_cols'], saved['ui_cols'])
        self.assertEqual(result['csv_cols'], saved['csv_cols'])
        self.assertEqual(result['pdf_cols'], saved['pdf_cols'])
        self.assertEqual(result['column_order'][:3], saved['column_order'])
        self.assertEqual(result['column_widths'], saved['column_widths'])
        self.assertEqual(list(COLUMNS)[12], 'LED')
        self.assertEqual([COLUMNS[code] for code in POOL_COLUMNS],
                         ['Pool 1', 'Worker 1', 'Pool 2', 'Worker 2', 'Pool 3', 'Worker 3'])

    def test_old_builtin_pools_view_expands_but_overview_stays_compact(self):
        legacy = ['IP', 'Model', 'Status', 'Real HR', 'Pool', 'Worker']
        result = normalize(dict(table_preset='pools', ui_cols=legacy, columns_version=2))
        self.assertEqual(result['ui_cols'], TABLE_PRESETS['pools'][1])
        older = normalize(dict(table_preset='pools', ui_cols=legacy, columns_version=1))
        self.assertTrue(set(POOL_COLUMNS).issubset(older['ui_cols']))
        self.assertIn('LED', older['ui_cols'])
        self.assertNotIn('Pool2', defaults()['ui_cols'])


@unittest.skipUnless(DESKTOP_AVAILABLE, 'Desktop dependencies not installed')
class PoolColumnsGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gemini_gui
        cls.gui = gemini_gui
        cls.app = gemini_gui.QApplication.instance() or gemini_gui.QApplication([])

    def window(self):
        from miner_scanner.access import AccessProfiles
        with patch.object(self.gui.AccessStore, 'load', return_value=AccessProfiles([])):
            window = self.gui.GeminiApp(settings=dict(table_preset='pools',
                ui_cols=TABLE_PRESETS['pools'][1], auto_fit_columns=False), ranges=[])
        self.addCleanup(window.close)
        return window

    def test_all_slots_display_full_values_and_old_row_update_clears_backup_cells(self):
        window = self.window()
        window.apply_table_preset()
        self.assertEqual(window.app_settings['density'], 'compact')
        record = dict(IP='192.0.2.1', Model='Demo', Status='Running')
        record.update({code: 'full.' + code + '.value' for code in POOL_COLUMNS})
        record['Worker3'] = 'owner.' + 'worker-name-' * 80
        window.on_result([record])
        for code in POOL_COLUMNS:
            index = list(COLUMNS).index(code)
            self.assertFalse(window.table.isColumnHidden(index))
            self.assertEqual(window.table.item(0, index).text(), record[code])
            self.assertEqual(window.table.item(0, index).toolTip(), record[code])
        window.on_result([dict(IP='192.0.2.1', Model='Demo', Worker='primary')])
        for code in ('Pool2', 'Worker2', 'Pool3', 'Worker3'):
            self.assertEqual(window.table.item(0, list(COLUMNS).index(code)).text(), '—')

    def test_third_worker_search_and_sort_survive_language_switch(self):
        window = self.window()
        window.on_result([dict(IP='192.0.2.1', Model='Demo', Worker3='zulu.third'),
                          dict(IP='192.0.2.2', Model='Demo', Worker3='alpha.third')])
        column = list(COLUMNS).index('Worker3')
        window.table.sortItems(column, self.gui.Qt.SortOrder.AscendingOrder)
        self.assertEqual(window.table.item(0, 0).text(), '192.0.2.2')
        window.search_input.setText('zulu.third')
        self.assertTrue(window.table.isRowHidden(0))
        self.assertFalse(window.table.isRowHidden(1))
        window.change_application_language('en')
        self.assertEqual(window.table.horizontalHeaderItem(column).text(), 'Worker 3')
        self.assertEqual(window.table.item(1, column).text(), 'zulu.third')
        self.assertFalse(window.table.isRowHidden(1))

    def test_settings_select_slots_independently_for_table_and_reports(self):
        from PyQt6.QtCore import Qt
        from desktop_ui.settings_dialog import SettingsDialog
        dialog = SettingsDialog(defaults())
        self.addCleanup(dialog.close)
        dialog.table_preset.setCurrentIndex(dialog.table_preset.findData('pools'))
        dialog.apply_table_preset()
        self.assertEqual(dialog.density.currentData(), 'compact')
        for kind in ('ui', 'pdf', 'csv'):
            listing = getattr(dialog, 'list_' + kind + '_cols')
            entries = {listing.item(i).data(Qt.ItemDataRole.UserRole): listing.item(i)
                       for i in range(listing.count())}
            self.assertTrue(set(POOL_COLUMNS).issubset(entries))
            entries['Worker3'].setCheckState(Qt.CheckState.Checked)
            entries['Pool3'].setCheckState(Qt.CheckState.Unchecked)
        for kind in ('pdf', 'csv'):
            control = getattr(dialog, kind + '_sort')
            self.assertGreaterEqual(control.findData('Worker3'), 0)
            control.setCurrentIndex(control.findData('Worker3'))
        dialog.save_and_close()
        self.assertEqual(dialog.result(), dialog.DialogCode.Accepted)
        for key in ('ui_cols', 'pdf_cols', 'csv_cols'):
            self.assertIn('Worker3', dialog.settings[key])
            self.assertNotIn('Pool3', dialog.settings[key])
        self.assertEqual(dialog.settings['csv_sort'], 'Worker3')

    def test_pdf_can_export_selected_second_and_third_pool_pairs(self):
        window = self.window()
        record = dict(IP='192.0.2.1', Model='Demo', Algo='SHA-256', Status='Running',
                      Real='100 TH/s', RawHash=1e14)
        record.update({code: code.lower() + '.invalid' for code in POOL_COLUMNS})
        window.on_result([record])
        seen = []
        original = self.gui.PDFReport

        class RecordingPDF(original):
            def cell(self, *args, **kwargs):
                seen.append(str(args[2] if len(args) > 2 else kwargs.get('text', '')))
                return super().cell(*args, **kwargs)

        with tempfile.TemporaryDirectory() as folder:
            window.app_settings.update(export_dir=folder, pdf_cols=['IP', *POOL_COLUMNS],
                                       copy_pdf=False)
            with patch.object(self.gui, 'PDFReport', RecordingPDF), \
                 patch.object(self.gui.QMessageBox, 'information'), \
                 patch.object(self.gui.QMessageBox, 'critical') as failure:
                window.export_pdf_pro()
                failure.assert_not_called()
            files = list(Path(folder).glob('*.pdf'))
            self.assertEqual(len(files), 1)
            self.assertTrue(files[0].read_bytes().startswith(b'%PDF'))
            for code in POOL_COLUMNS:
                self.assertIn(COLUMNS[code], seen)
                self.assertIn(record[code], seen)
