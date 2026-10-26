"""Regression checks for bounded popups and typed font sizes."""
import importlib.util
import os
from pathlib import Path
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


@unittest.skipUnless(importlib.util.find_spec('PyQt6'), 'Desktop dependencies not installed')
class FontControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtGui import QFontDatabase
        cls.app = QApplication.instance() or QApplication([])
        for filename in ('segoeui.ttf', 'segoeuib.ttf', 'arial.ttf'):
            QFontDatabase.addApplicationFont(str(Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / filename))

    def setUp(self):
        from desktop_ui.i18n import set_language
        from desktop_ui.theme import apply_theme
        set_language('ru')
        apply_theme(self.app)
        self.addCleanup(set_language, 'ru')
        self.addCleanup(apply_theme, self.app)

    def dialog(self):
        from desktop_ui.settings_dialog import SettingsDialog
        dialog = SettingsDialog({})
        self.addCleanup(dialog.close)
        dialog.show()
        self.app.processEvents()
        return dialog

    def test_font_popup_remains_bounded_scrolls_and_keeps_current_font_visible(self):
        from PyQt6.QtCore import QStringListModel, QPoint, Qt
        from PyQt6.QtTest import QTest
        from PyQt6.QtWidgets import QAbstractItemView
        from desktop_ui.font_controls import CompactFontComboBox
        # A long list and one very wide name reproduce the reported geometry
        # without depending on how many fonts the CI runner has installed.
        dialog = self.dialog()
        combo = dialog.font_family
        self.assertIsInstance(combo, CompactFontComboBox)
        choices = ['Example font ' + str(index) for index in range(100)]
        choices[50] += ' with an exceptionally long name' * 8
        model = QStringListModel(choices, combo)
        combo.setModel(model)
        combo.setCurrentIndex(95)
        combo.showPopup()
        self.app.processEvents()
        popup, view = combo.view().window(), combo.view()
        try:
            self.assertEqual(popup.width(), combo.width())
            self.assertLessEqual(popup.height(), dialog.screen().availableGeometry().height() // 2)
            self.assertLess(popup.height(), 350)
            self.assertTrue(dialog.screen().availableGeometry().contains(popup.geometry()))
            below = combo.mapToGlobal(QPoint(0, combo.height())).y()
            if below + popup.height() <= dialog.screen().availableGeometry().bottom() + 1:
                self.assertEqual(popup.y(), below)
            self.assertGreater(view.verticalScrollBar().maximum(), 0)
            selected = model.index(95, 0)
            self.assertTrue(view.viewport().rect().intersects(view.visualRect(selected)))
            # Scrolling to an earlier font does not resize the popup to the model.
            view.scrollTo(model.index(0, 0), QAbstractItemView.ScrollHint.PositionAtTop)
            self.app.processEvents()
            self.assertEqual(popup.width(), combo.width())
            QTest.keyClick(view, Qt.Key.Key_Down)
            QTest.keyClick(view, Qt.Key.Key_Return)
            self.app.processEvents()
            self.assertEqual(combo.currentIndex(), 96)
            self.assertFalse(popup.isVisible())
        finally:
            combo.hidePopup()

    def test_sizes_have_presets_and_allow_manual_values_without_stale_selection(self):
        from PyQt6.QtTest import QTest
        from PyQt6.QtCore import Qt
        from desktop_ui.font_controls import FontSizeComboBox
        dialog = self.dialog()
        control = dialog.font_size
        self.assertIsInstance(control, FontSizeComboBox)
        self.assertTrue(control.isEditable())
        self.assertIn('16', [control.itemText(i) for i in range(control.count())])
        self.assertEqual(control.findText('15'), -1)
        control.showPopup()
        self.app.processEvents()
        self.assertEqual(control.view().window().width(), control.width())
        self.assertLess(control.view().window().height(), 300)
        QTest.keyClick(control.view(), Qt.Key.Key_Down)
        QTest.keyClick(control.view(), Qt.Key.Key_Return)
        self.assertEqual(control.currentText(), '14')
        self.assertEqual(dialog.result(), 0)
        control.lineEdit().setFocus()
        control.lineEdit().selectAll()
        QTest.keyClicks(control.lineEdit(), '15')
        QTest.keyClick(control.lineEdit(), Qt.Key.Key_Tab)
        self.assertEqual(control.value(), 15)
        self.assertEqual(dialog.font_preview.font().pixelSize(), 15)
        dialog.table_font_size.setCurrentIndex(dialog.table_font_size.findText('22'))
        dialog.save_and_close()
        self.assertEqual(dialog.result(), 1)
        self.assertEqual(dialog.settings['font_size'], 15)
        self.assertEqual(dialog.settings['table_font_size'], 22)

    def test_invalid_or_empty_input_is_not_silently_saved_as_previous_size(self):
        dialog = self.dialog()
        for text in ('', '9', '99', 'x'):
            with self.subTest(text=text):
                dialog.font_size.setEditText(text)
                dialog.save_and_close()
                self.assertEqual(dialog.result(), 0)
                self.assertFalse(dialog.font_error.isHidden())
                self.assertIn('11', dialog.font_error.text())
                self.assertEqual(dialog.settings['font_size'], 13)
        dialog.font_size.setEditText('17')
        self.assertTrue(dialog.font_error.isHidden())
        dialog.table_font_size.setEditText('9')
        dialog.save_and_close()
        self.assertEqual(dialog.result(), 0)
        self.assertIn('24', dialog.font_error.text())
        dialog.table_font_size.setEditText('23')
        dialog.save_and_close()
        self.assertEqual(dialog.settings['font_size'], 17)
        self.assertEqual(dialog.settings['table_font_size'], 23)

    def test_reset_replaces_manual_input_even_when_preset_index_has_not_changed(self):
        dialog = self.dialog()
        dialog.font_size.setEditText('')
        dialog.table_font_size.setEditText('99')
        dialog.reset_fonts_button.click()
        self.assertEqual(dialog.font_size.currentText(), '13')
        self.assertEqual(dialog.table_font_size.currentText(), '13')
        dialog.save_and_close()
        self.assertEqual(dialog.result(), 1)
