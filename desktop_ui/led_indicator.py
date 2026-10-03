"""Icon-only locate indicator; full state stays accessible and exportable."""
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QStyledItemDelegate, QStyleOptionViewItem, QStyle, QTableWidgetItem

STATE_ROLE = Qt.ItemDataRole.UserRole + 2


def led_item(row):
    state = row.get('IdentifyEnabled')
    if row.get('Stale') or type(state) is not bool:
        state = None
    text = 'Включена' if state is True else 'Выключена' if state is False else 'Неизвестно'
    item = QTableWidgetItem(text)
    item.setData(STATE_ROLE, state)
    observed = (row.get('Telemetry') or {}).get('observed_at')
    item.setData(Qt.ItemDataRole.AccessibleTextRole, 'Подсветка: ' + text)
    hint = text + '. Последнее состояние по API. Обновляется при сканировании и после команды.'
    if state is None:
        hint = 'API не подтвердил состояние подсветки. Неизвестно не означает выключена.'
    if observed:
        hint += '\nВремя измерения: ' + str(observed)
    item.setToolTip(hint)
    return item


class LedDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        view = QStyleOptionViewItem(option)
        self.initStyleOption(view, index)
        view.text = ''
        style = view.widget.style() if view.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, view, painter, view.widget)
        state = index.data(STATE_ROLE)
        dark = view.palette.window().color().lightness() < 128
        color = QColor('#ffc46b' if dark else '#985200') if state is True else QColor('#aebccc' if dark else '#65758a')
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(color, 1.5))
        cx, cy = view.rect.center().x(), view.rect.center().y()
        if state is None:
            painter.setPen(view.palette.text().color())
            painter.drawText(view.rect, int(Qt.AlignmentFlag.AlignCenter), '?')
        else:
            painter.setBrush(color if state else Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(cx-4, cy-7, 8, 9))
            painter.drawLine(cx-2, cy+4, cx+2, cy+4)
            painter.drawLine(cx-1, cy+6, cx+1, cy+6)
        painter.restore()
