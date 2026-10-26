"""Compact font lists and spreadsheet-style editable size selectors."""
from PyQt6.QtCore import QPoint, QLocale, Qt, pyqtSignal
from PyQt6.QtGui import QIntValidator
from PyQt6.QtWidgets import QAbstractItemView, QComboBox, QFontComboBox


class _BoundedPopup:
    def showPopup(self):
        if not self.count():
            return
        view = self.view()
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        view.setTextElideMode(Qt.TextElideMode.ElideRight)
        available = self.screen().availableGeometry()
        row_height = max(24, view.sizeHintForRow(0), self.fontMetrics().height() + 8)
        rows = min(self.count(), self.maxVisibleItems())
        height = min(rows * row_height + 18, available.height() // 2)
        popup = view.window()
        # Limiting the view alone does not constrain Qt's popup container.
        popup.setFixedSize(min(self.width(), available.width()), height)
        super().showPopup()
        below = self.mapToGlobal(QPoint(0, self.height()))
        above = self.mapToGlobal(QPoint(0, 0)).y() - popup.height()
        y = below.y() if below.y() + popup.height() <= available.bottom() + 1 else above
        x = max(available.left(), min(below.x(), available.right() + 1 - popup.width()))
        y = max(available.top(), min(y, available.bottom() + 1 - popup.height()))
        popup.move(x, y)
        view.scrollTo(view.currentIndex(), QAbstractItemView.ScrollHint.PositionAtCenter)


class CompactFontComboBox(_BoundedPopup, QFontComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMaxVisibleItems(8)


class FontSizeComboBox(_BoundedPopup, QComboBox):
    valueChanged = pyqtSignal(int)

    def __init__(self, minimum, maximum, value, parent=None):
        super().__init__(parent)
        self._minimum, self._maximum = minimum, maximum
        self._value = max(minimum, min(maximum, value))
        self.setObjectName('FontSizeCombo')
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setCompleter(None)
        self.setMaxVisibleItems(8)
        self.setFixedWidth(max(100, self.fontMetrics().horizontalAdvance(str(maximum)) + 54))
        self.addItems([str(size) for size in (10, 11, 12, 13, 14, 16, 18, 20, 22, 24)
                       if minimum <= size <= maximum])
        validator = QIntValidator(minimum, maximum, self)
        locale = QLocale.c()
        locale.setNumberOptions(QLocale.NumberOption.RejectGroupSeparator)
        validator.setLocale(locale)
        self.setValidator(validator)
        self.lineEdit().setMaxLength(len(str(maximum)))
        self.currentTextChanged.connect(self._update_value)
        self.setValue(value)

    def _update_value(self):
        if self.lineEdit().hasAcceptableInput():
            self._value = int(self.currentText())
            self.valueChanged.emit(self._value)

    def value(self):
        """Last valid value for preview; Save separately validates current input."""
        return self._value

    def setValue(self, value):
        text = str(max(self._minimum, min(self._maximum, value)))
        index = self.findText(text)
        if index >= 0:
            self.setCurrentIndex(index)
        self.setEditText(text)
