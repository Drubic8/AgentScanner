"""Subnet editor with a live, offline preview of the exact scan targets."""
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                             QPlainTextEdit, QPushButton, QVBoxLayout)

from .network_groups import preview_ranges, split_ranges, validate_name


class IPRangeDialog(QDialog):
    def __init__(self, name="", ranges=None, parent=None, *, existing_names=()):
        super().__init__(parent)
        self.existing_names = tuple(existing_names)
        self.setWindowTitle("Изменить сеть" if name else "Добавить сеть")
        self.resize(680, 620)
        self.setMinimumSize(540, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 22)
        layout.setSpacing(14)

        title = QLabel(self.windowTitle())
        title.setObjectName("DialogTitle")
        layout.addWidget(title)
        description = QLabel("Объедините подсети и отдельные IP в одну группу для сканирования.")
        description.setObjectName("Muted")
        description.setWordWrap(True)
        layout.addWidget(description)

        label = QLabel("Название сети")
        self.le_name = QLineEdit(name)
        self.le_name.setMaxLength(120)
        self.le_name.setPlaceholderText("Площадка 1 · контейнер А")
        label.setBuddy(self.le_name)
        layout.addWidget(label)
        layout.addWidget(self.le_name)

        label = QLabel("Адреса для опроса")
        self.te_ranges = QPlainTextEdit()
        self.te_ranges.setObjectName("RangeEditor")
        self.te_ranges.setAccessibleName("IP-адреса и диапазоны")
        self.te_ranges.setPlaceholderText("192.168.1.0/24\n192.168.2.10-50\n10.0.0.15")
        self.te_ranges.setPlainText("\n".join(ranges) if isinstance(ranges, list) else str(ranges or ""))
        self.te_ranges.setTabChangesFocus(True)
        label.setBuddy(self.te_ranges)
        layout.addWidget(label)
        layout.addWidget(self.te_ranges, 1)
        help_label = QLabel("Каждый диапазон — с новой строки. При вставке можно использовать запятые и точку с запятой. Поддерживаются IPv4, CIDR и интервалы IP.")
        help_label.setObjectName("Muted")
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        preview = QFrame()
        preview.setObjectName("RangePreview")
        preview_layout = QVBoxLayout(preview)
        preview_layout.setContentsMargins(16, 14, 16, 14)
        self.summary = QLabel()
        self.summary.setObjectName("RangeSummary")
        self.details = QLabel()
        self.details.setObjectName("Muted")
        self.details.setWordWrap(True)
        preview_layout.addWidget(self.summary)
        preview_layout.addWidget(self.details)
        layout.addWidget(preview)
        self.error = QLabel()
        self.error.setObjectName("ValidationError")
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        layout.addWidget(self.error)

        actions = QHBoxLayout()
        hint = QLabel("Проверка адресов без обращения к устройствам")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        actions.addWidget(hint, 1)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        self.btn_save = QPushButton("Сохранить сеть")
        self.btn_save.setProperty("primary", True)
        self.btn_save.setDefault(True)
        self.btn_save.clicked.connect(self.validate_and_accept)
        actions.addWidget(cancel)
        actions.addWidget(self.btn_save)
        layout.addLayout(actions)

        self.validation_timer = QTimer(self)
        self.validation_timer.setSingleShot(True)
        self.validation_timer.setInterval(180)
        self.validation_timer.timeout.connect(self.update_preview)
        self.le_name.textChanged.connect(self.schedule_preview)
        self.te_ranges.textChanged.connect(self.schedule_preview)
        self.update_preview()
        self.le_name.setFocus()

    def schedule_preview(self):
        self.btn_save.setEnabled(False)
        self.validation_timer.start()

    def update_preview(self):
        self.validation_timer.stop()
        self.error.clear()
        try:
            preview = preview_ranges(self.te_ranges.toPlainText())
        except ValueError as exc:
            self.summary.setText("Адреса ещё не готовы к сканированию")
            self.details.setText("До 4096 уникальных IPv4-адресов в одной группе.")
            if self.te_ranges.toPlainText().strip():
                self.error.setText(str(exc))
            self.btn_save.setEnabled(False)
            return False
        self.summary.setText(f"{len(preview.addresses):,} IP-адресов для опроса".replace(",", " "))
        details = f"Диапазонов: {len(preview.ranges)}. От {preview.addresses[0]} до {preview.addresses[-1]}."
        if preview.repeated:
            details += f" Повторяющихся адресов: {preview.repeated}; каждый будет опрошен один раз."
        details += " Для CIDR /30 и шире адрес сети и broadcast исключены."
        self.details.setText(details)
        try:
            validate_name(self.le_name.text(), self.existing_names)
        except ValueError as exc:
            self.error.setText(str(exc))
            self.btn_save.setEnabled(False)
            return False
        self.btn_save.setEnabled(True)
        return True

    def validate_and_accept(self):
        if self.update_preview():
            self.accept()

    def get_data(self):
        return self.le_name.text().strip(), split_ranges(self.te_ranges.toPlainText())
