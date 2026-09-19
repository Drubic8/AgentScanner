"""Saved networks: focus selects an editor target, checkboxes select scan targets."""
from copy import deepcopy

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QHBoxLayout, QLabel,
                             QLineEdit, QListWidget, QListWidgetItem, QPushButton,
                             QVBoxLayout, QWidget)

from miner_scanner.ranges import expand_ranges
from .network_groups import selected_ranges


class RangesPanel(QWidget):
    changed = pyqtSignal(list)
    add_requested = pyqtSignal()
    edit_requested = pyqtSignal(int)
    delete_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.groups = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        header = QHBoxLayout()
        title = QLabel("Сети для сканирования")
        title.setObjectName("RangeSectionTitle")
        header.addWidget(title)
        header.addStretch()
        layout.addLayout(header)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по названию или IP")
        self.search.setAccessibleName("Поиск сохранённых сетей")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.filter_groups)
        layout.addWidget(self.search)
        self.select_all = QCheckBox("Все сети")
        self.select_all.setToolTip("Включить или выключить все сохранённые сети, в том числе скрытые поиском")
        self.select_all.clicked.connect(self.toggle_all)
        layout.addWidget(self.select_all)
        self.list_ranges = QListWidget()
        self.list_ranges.setObjectName("NetworkList")
        self.list_ranges.setMinimumHeight(110)
        self.list_ranges.setAccessibleName("Сети; отметьте галочками сети для сканирования")
        self.list_ranges.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_ranges.itemChanged.connect(self.on_item_changed)
        self.list_ranges.currentRowChanged.connect(self.update_actions)
        self.list_ranges.itemDoubleClicked.connect(lambda item: self.edit_requested.emit(item.data(Qt.ItemDataRole.UserRole)))
        layout.addWidget(self.list_ranges, 1)
        self.empty = QLabel("Сохранённых сетей пока нет. Добавьте первую сеть.")
        self.empty.setObjectName("Muted")
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty)
        self.summary = QLabel()
        self.summary.setObjectName("SelectionCount")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.hint = QLabel("Галочка включает сеть в сканирование. Выбор строки — для изменения.")
        self.hint.setObjectName("Muted")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        add = QPushButton("Добавить сеть")
        add.setProperty("primary", True)
        add.clicked.connect(self.add_requested)
        layout.addWidget(add)
        actions = QHBoxLayout()
        self.edit_button = QPushButton("Изменить")
        self.delete_button = QPushButton("Удалить")
        self.edit_button.clicked.connect(lambda: self.edit_requested.emit(self.current_index()))
        self.delete_button.clicked.connect(lambda: self.delete_requested.emit(self.current_index()))
        actions.addWidget(self.edit_button)
        actions.addWidget(self.delete_button)
        layout.addLayout(actions)
        self.update_actions()

    def current_index(self):
        item = self.list_ranges.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else -1

    def set_groups(self, groups, current=None):
        if current is None:
            current = self.current_index()
        self.groups = deepcopy(groups)
        self.list_ranges.blockSignals(True)
        self.list_ranges.clear()
        for index, group in enumerate(groups):
            ranges = group.get("ranges", [])
            description = ranges[0] if len(ranges) == 1 else f"Диапазонов: {len(ranges)}"
            item = QListWidgetItem(f"{group.get('name', 'Сеть')}\n{description}")
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setToolTip("\n".join([group.get("name", "Сеть"), *ranges]))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if group.get("enabled", True) else Qt.CheckState.Unchecked)
            self.list_ranges.addItem(item)
        if 0 <= current < len(groups):
            self.list_ranges.setCurrentRow(current)
        self.list_ranges.blockSignals(False)
        enabled = sum(group.get("enabled", True) for group in groups)
        self.select_all.setEnabled(bool(groups))
        self.select_all.setCheckState(Qt.CheckState.Checked if enabled == len(groups) and groups else Qt.CheckState.PartiallyChecked if enabled else Qt.CheckState.Unchecked)
        try:
            total = len(expand_ranges(selected_ranges(groups)))
            self.summary.setText(f"Выбрано сетей: {enabled} из {len(groups)}\nIP-адресов: {total:,}".replace(",", " "))
        except ValueError:
            self.summary.setText(f"Выбрано сетей: {enabled} из {len(groups)}\nПроверьте адреса или лимит 4096 IP.")
        self.filter_groups()
        self.update_actions()

    def filter_groups(self):
        query = self.search.text().strip().casefold()
        visible = 0
        for index, group in enumerate(self.groups):
            matches = query in " ".join([group.get("name", ""), *group.get("ranges", [])]).casefold()
            self.list_ranges.item(index).setHidden(not matches)
            visible += int(matches)
        self.empty.setVisible(visible == 0)
        self.empty.setText("Сети не найдены. Измените поиск." if self.groups else "Сохранённых сетей пока нет. Добавьте первую сеть.")
        self.hint.setText("Поиск скрывает строки, но сохраняет галочки. Сканируются все отмеченные сети." if query else "Галочка включает сеть в сканирование. Выбор строки — для изменения.")
        self.update_actions()

    def update_actions(self):
        item = self.list_ranges.currentItem()
        available = item is not None and not item.isHidden()
        self.edit_button.setEnabled(available)
        self.delete_button.setEnabled(available)

    def on_item_changed(self, item):
        candidate = deepcopy(self.groups)
        candidate[item.data(Qt.ItemDataRole.UserRole)]["enabled"] = item.checkState() == Qt.CheckState.Checked
        self.changed.emit(candidate)

    def toggle_all(self, checked):
        candidate = deepcopy(self.groups)
        for group in candidate:
            group["enabled"] = checked
        self.changed.emit(candidate)
