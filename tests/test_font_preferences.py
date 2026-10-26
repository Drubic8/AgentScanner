"""Typography saves atomically with settings and reaches existing desktop views."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from desktop_ui.preferences import defaults, normalize, load_json, write_json

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


class FontStorageTests(unittest.TestCase):
    def test_old_and_invalid_settings_have_usable_defaults(self):
        self.assertEqual(normalize({'theme': 'dark'})['font_size'], 13)
        for value in (None, True, 'invalid', float('inf')):
            with self.subTest(value=value):
                settings = normalize({'font_size': value, 'table_font_size': value})
                self.assertEqual((settings['font_size'], settings['table_font_size']), (13, 13))
        for family in ('', '   ', 'bad\nfamily', 'x' * 129, None):
            self.assertEqual(normalize({'font_family': family})['font_family'], 'Segoe UI')
        settings = normalize({'font_size': 200, 'table_font_size': -1})
        self.assertEqual((settings['font_size'], settings['table_font_size']), (20, 10))

    def test_selected_fonts_survive_file_roundtrip_with_other_preferences(self):
        settings = normalize(dict(defaults(), font_family='Arial', font_size=16, table_font_size=18,
                                  export_csv_dir='E:/Reports', density='compact'))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'app_settings.json'
            write_json(path, settings)
            self.assertEqual(normalize(load_json(path)), settings)


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in ('PyQt6', 'pandas', 'fpdf')),
                     'Desktop dependencies not installed')
class FontInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gemini_gui
        from PyQt6.QtGui import QFontDatabase
        cls.gui = gemini_gui
        cls.app = gemini_gui.QApplication.instance() or gemini_gui.QApplication([])
        # Windows' offscreen plugin needs explicit font registration.
        directory = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
        for filename in ('segoeui.ttf', 'segoeuib.ttf', 'arial.ttf'):
            QFontDatabase.addApplicationFont(str(directory / filename))

    def setUp(self):
        from desktop_ui.i18n import set_language
        from desktop_ui.theme import apply_theme
        set_language('ru')
        apply_theme(self.app)
        self.network = patch('requests.sessions.Session.request', side_effect=AssertionError('Unexpected network'))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.addCleanup(apply_theme, self.app)
        self.addCleanup(set_language, 'ru')

    def dialog(self, settings=None):
        from desktop_ui.settings_dialog import SettingsDialog
        dialog = SettingsDialog(settings or defaults())
        self.addCleanup(dialog.close)
        return dialog

    def window(self, **settings):
        from miner_scanner.access import AccessProfiles
        with patch.object(self.gui.AccessStore, 'load', return_value=AccessProfiles([])):
            window = self.gui.GeminiApp(settings=dict(defaults(), **settings), ranges=[])
        self.addCleanup(window.close)
        return window

    def test_preview_cancel_save_and_font_only_reset(self):
        from PyQt6.QtGui import QFont
        dialog = self.dialog(dict(defaults(), export_csv_dir='E:/Reports'))
        dialog.font_family.setCurrentFont(QFont('Arial'))
        dialog.font_size.setValue(16)
        dialog.table_font_size.setValue(22)
        dialog.font_preview.ensurePolished()
        dialog.table_font_preview.ensurePolished()
        self.assertEqual(dialog.font_preview.font().pixelSize(), 16)
        self.assertEqual(dialog.table_font_preview.font().pixelSize(), 22)
        self.assertEqual(self.app.font().pixelSize(), 13)
        dialog.reject()
        self.assertEqual(dialog.settings['font_size'], 13)
        saved = self.dialog(dict(defaults(), export_csv_dir='E:/Reports'))
        saved.font_family.setCurrentFont(QFont('Arial'))
        saved.font_size.setValue(16)
        saved.table_font_size.setValue(22)
        saved.save_and_close()
        self.assertEqual(saved.settings['font_family'], 'Arial')
        self.assertEqual(saved.settings['font_size'], 16)
        self.assertEqual(saved.settings['table_font_size'], 22)
        reset = self.dialog(saved.settings)
        reset.reset_fonts_button.click()
        reset.save_and_close()
        self.assertEqual(reset.settings['font_size'], 13)
        self.assertEqual(reset.settings['table_font_size'], 13)
        self.assertEqual(reset.settings['export_csv_dir'], 'E:/Reports')

    def test_existing_rows_inherit_font_and_keep_selection_and_manual_width(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QStyleOptionViewItem
        window = self.window(auto_fit_columns=False)
        window.on_result([dict(IP='192.0.2.21', Model='Antminer S21', Status='Running', Error='FAN ERR')])
        window.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
        window.table.setColumnWidth(1, 210)
        window.app_settings.update(font_family='Arial', font_size=16, table_font_size=24, density='compact')
        window.apply_theme()
        self.app.processEvents()
        self.assertEqual(window.table.font().family(), 'Arial')
        self.assertEqual(window.table.font().pixelSize(), 24)
        self.assertEqual(window.btn_scan.font().pixelSize(), 16)
        self.assertEqual(window.log_dialog.table.font().pixelSize(), 24)
        self.assertEqual(window.table.horizontalHeader().font().pixelSize(), 24)
        self.assertGreaterEqual(window.table.rowHeight(0), window.table.fontMetrics().height() + 8)
        self.assertGreaterEqual(window.log_dialog.table.verticalHeader().defaultSectionSize(),
                                window.log_dialog.table.fontMetrics().height() + 8)
        for column in (0, 1, 3, 4):
            option = QStyleOptionViewItem()
            option.initFrom(window.table)
            # QAbstractItemView sets the view font; initFrom alone leaves the
            # application font in QStyleOptionViewItem.font.
            option.font = window.table.font()
            option.widget = window.table
            window.table.itemDelegate().initStyleOption(option, window.table.model().index(0, column))
            self.assertEqual(option.font.bold(), column in (3, 4))
            self.assertEqual(option.font.pixelSize(), 24)
            self.assertEqual(option.font.family(), 'Arial')
        # Reapplying appearance never replaces device records or selection.
        self.assertEqual(window.table.item(0, 0).checkState(), Qt.CheckState.Checked)
        self.assertEqual(window.scan_data[0]['IP'], '192.0.2.21')
        window.table.setColumnWidth(1, 210)
        window.app_settings.update(table_font_size=18)
        window.apply_theme()
        self.assertEqual(window.table.columnWidth(1), 210)
        window.change_application_language('en')
        self.assertEqual(window.table.font().pixelSize(), 18)
        self.assertEqual(window.btn_scan.text(), 'Start scan')

    def test_large_interface_font_does_not_clip_sidebar_controls(self):
        window = self.window(font_size=20, table_font_size=24)
        window.show()
        self.app.processEvents()
        self.assertGreaterEqual(window.sidebar_scroll.viewport().width(),
                                window.sidebar_scroll.widget().minimumSizeHint().width())

    def test_saved_settings_apply_on_reopen_and_missing_family_falls_back(self):
        from PyQt6.QtGui import QFontDatabase
        window = self.window(font_family='Arial', font_size=17, table_font_size=20)
        self.assertEqual(window.btn_scan.font().pixelSize(), 17)
        self.assertEqual(window.table.font().pixelSize(), 20)
        reopened = self.window(**normalize(window.app_settings))
        self.assertEqual(reopened.table.font().family(), 'Arial')
        self.assertEqual(reopened.table.font().pixelSize(), 20)
        reopened.app_settings['font_family'] = "unavailable'; color: red; /*"
        reopened.apply_theme()
        self.assertIn(reopened.table.font().family(), QFontDatabase.families())

    def test_changed_table_font_refits_only_when_enabled(self):
        window = self.window()
        window.on_result([dict(IP='192.0.2.21', Model='Antminer S21')])
        with patch.object(window, 'fit_table_columns') as fit:
            window.app_settings['table_font_size'] = 19
            window.apply_theme()
            fit.assert_called_once_with()
        with patch.object(window, 'fit_table_columns') as fit:
            window.apply_theme()
            fit.assert_not_called()

    def test_save_through_main_window_persists_and_applies_without_restart(self):
        from desktop_ui.settings_dialog import SettingsDialog
        window = self.window()

        def save_dialog(dialog):
            dialog.font_size.setValue(15)
            dialog.table_font_size.setValue(21)
            dialog.save_and_close()
            return dialog.result()

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'app_settings.json'
            with patch.object(self.gui, 'SETTINGS_FILE', path), patch.object(SettingsDialog, 'exec', save_dialog):
                window.open_settings_dialog()
            saved = normalize(load_json(path))
            self.assertEqual(saved['font_size'], 15)
            self.assertEqual(saved['table_font_size'], 21)
            self.assertEqual(window.table.font().pixelSize(), 21)
            self.assertEqual(window.btn_scan.font().pixelSize(), 15)
