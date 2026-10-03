"""Compact, accessible locate indicator: a lamp plus text, without busy motion."""
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
    hint = 'Последнее состояние по API. Обновляется при сканировании и после команды.'
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
        text = view.text
        view.text = ''
        style = view.widget.style() if view.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, view, painter, view.widget)
        state = index.data(STATE_ROLE)
        dark = view.palette.window().color().lightness() < 128
        color = QColor('#ffc46b' if dark else '#985200') if state is True else view.palette.mid().color()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(color, 1.5))
        painter.setBrush(color if state is True else Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(view.rect.left()+9, view.rect.center().y()-4, 8, 8))
        painter.setPen(view.palette.text().color())
        rect = view.rect.adjusted(24, 0, -4, 0)
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                         view.fontMetrics.elidedText(text, Qt.TextElideMode.ElideRight, rect.width()))
        painter.restore()
