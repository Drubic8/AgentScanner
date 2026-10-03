"""Compact totals with keyboard-accessible, scrollable full details."""
from html import escape
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QSizePolicy, QTabWidget, QVBoxLayout, QWidget)


class ElidedLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.full_text = ""
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)
        self.setObjectName("Muted")

    def set_full_text(self, text):
        self.full_text = text
        self.setAccessibleName(text)
        self.setToolTip("<qt>" + escape(text) + "</qt>")
        self._elide()

    def _elide(self):
        self.setText(self.fontMetrics().elidedText(self.full_text, Qt.TextElideMode.ElideRight, max(0, self.width())))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()


class SummaryPanel(QFrame):
    expanded_changed = pyqtSignal(bool)

    def __init__(self, parent=None, *, expanded=False):
        super().__init__(parent)
        self.setObjectName("SummaryPanel")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(18)
        self.metrics = []
        for title, weight in (("Устройства", 2), ("Модели", 2), ("Хешрейт", 3)):
            metric = QWidget()
            metric.setMinimumWidth(0)
            metric.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            layout = QVBoxLayout(metric)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(3)
            line = QHBoxLayout()
            line.setSpacing(8)
            label = QLabel(title)
            label.setObjectName("Muted")
            value = ElidedLabel()
            value.setObjectName("SummaryValue")
            value.setTextFormat(Qt.TextFormat.PlainText)
            line.addWidget(label)
            line.addWidget(value, 1)
            layout.addLayout(line)
            hint = ElidedLabel()
            layout.addWidget(hint)
            row.addWidget(metric, weight)
            self.metrics.append((value, hint))
        self.toggle = QPushButton()
        self.toggle.setCheckable(True)
        self.toggle.setChecked(expanded)
        self.toggle.toggled.connect(self.set_expanded)
        row.addWidget(self.toggle)
        root.addLayout(row)
        self.details = QTabWidget()
        self.details.setObjectName("SummaryDetails")
        self.details.setFixedHeight(150)
        self.detail_texts = []
        for label in ("Состояния", "Модели", "Хешрейт"):
            view = QPlainTextEdit()
            view.setReadOnly(True)
            view.setTabChangesFocus(True)
            view.setAccessibleName("Итоги: " + label)
            self.details.addTab(view, label)
            self.detail_texts.append(view)
        self.details.setCurrentIndex(1)
        root.addWidget(self.details)
        self.set_expanded(expanded, emit=False)

    def set_expanded(self, expanded, *, emit=True):
        self.toggle.blockSignals(True)
        self.toggle.setChecked(expanded)
        self.toggle.blockSignals(False)
        self.toggle.setText("Скрыть итоги" if expanded else "Подробнее")
        self.toggle.setAccessibleName("Свернуть подробные итоги" if expanded else "Раскрыть подробные итоги сканирования")
        self.details.setVisible(expanded)
        self.updateGeometry()
        if emit:
            self.expanded_changed.emit(expanded)

    def update_totals(self, total, statuses, models, hashrates):
        states = [f"{key}: {value['val']}" for key, value in statuses.items() if key != "Всего устройств"]
        model_lines = [f"{key}: {value['val']}" for key, value in models.items()]
        hash_lines = [f"{key}: {value['val']}" for key, value in hashrates.items()]
        rate_value = "—"
        if len(hashrates) == 1:
            key, value = next(iter(hashrates.items()))
            rate_value = f"{value['val']} {key.partition(' · ')[2]}".strip()
        elif hashrates:
            rate_value = f"{len(hashrates)} групп"
        entries = ((str(total), states, "Ожидание результатов"),
                   (str(len(models)), model_lines, "Появятся после сканирования"),
                   (rate_value, hash_lines, "Нет актуальных измерений"))
        for (value, hint), details, (number, lines, empty) in zip(self.metrics, self.detail_texts, entries):
            value.set_full_text(number)
            hint.set_full_text(" · ".join(lines) or empty)
            text = "\n".join(lines) or empty
            if text != details.toPlainText():
                position = details.verticalScrollBar().value()
                details.setPlainText(text)
                details.verticalScrollBar().setValue(position)
        # Keep faults visible first when there is not enough room for every state.
        critical = ("Ошибка", "Неизвестно", "Устаревшие данные")
        state_summary = [f"{key}: {statuses[key]['val']}" for key in critical if key in statuses]
        state_summary.extend(f"{key}: {value['val']}" for key, value in statuses.items()
                             if key not in (*critical, "Всего устройств"))
        self.metrics[0][1].set_full_text(" · ".join(state_summary) or "Ожидание результатов")
