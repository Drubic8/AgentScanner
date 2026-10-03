"""Small Qt vector icons, independent of system emoji fonts."""
import math
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


def theme_icon(*, sun, color):
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(color), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    if sun:
        painter.drawEllipse(QPointF(12, 12), 4, 4)
        for index in range(8):
            angle = index * math.pi / 4
            painter.drawLine(QPointF(12 + 7 * math.cos(angle), 12 + 7 * math.sin(angle)),
                             QPointF(12 + 10 * math.cos(angle), 12 + 10 * math.sin(angle)))
    else:
        path = QPainterPath(QPointF(17, 3))
        path.cubicTo(4, 0, 0, 17, 10, 21)
        path.cubicTo(17, 24, 23, 17, 22, 13)
        path.cubicTo(14, 17, 9, 8, 17, 3)
        painter.drawPath(path)
    painter.end()
    return QIcon(pixmap)
