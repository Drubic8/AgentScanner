"""Saved subnet tree: folders organize targets; leaf checkboxes select scans."""
from .i18n import tr
from copy import deepcopy

from PyQt6.QtCore import Qt, pyqtSignal, QItemSelectionModel
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox,
    QPushButton, QStyle, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from miner_scanner.ranges import expand_ranges
from .network_groups import (folder_state, move_nodes, node_at, selected_ranges,
                             set_enabled, walk_networks, walk_nodes)


class NetworkTree(QTreeWidget):
    """Keep flat-list access for integrations while exposing native tree items."""
    def item(self, index):
        return self.topLevelItem(index)

    def count(self):
        return self.topLevelItemCount()

    def setCurrentRow(self, index, command=QItemSelectionModel.SelectionFlag.ClearAndSelect):
        self.setCurrentItem(self.topLevelItem(index), 0, command)


class RangesPanel(QWidget):
    changed = pyqtSignal(list)
    add_requested = pyqtSignal()
    folder_requested = pyqtSignal()
    edit_requested = pyqtSignal(object)
    delete_requested = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.groups, self.items = [], {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        title = QLabel(tr('Сети для сканирования'))
        title.setObjectName("RangeSectionTitle")
        layout.addWidget(title)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr('Поиск папки, сети или IP'))
        self.search.setAccessibleName(tr('Поиск сохранённых сетей и папок'))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.filter_groups)
        layout.addWidget(self.search)
        self.select_all = QCheckBox(tr('Все сети'))
        self.select_all.setToolTip(tr('Включить или выключить все сети, включая скрытые поиском'))
        self.select_all.clicked.connect(self.toggle_all)
        layout.addWidget(self.select_all)
        self.list_ranges = NetworkTree()
        self.list_ranges.setObjectName("NetworkTree")
        self.list_ranges.setColumnCount(1)
        self.list_ranges.setHeaderHidden(True)
        self.list_ranges.setIndentation(16)
        self.list_ranges.setMinimumHeight(160)
        self.list_ranges.setUniformRowHeights(True)
        self.list_ranges.setAccessibleName(tr('Дерево сетей; галочка папки включает все вложенные сети'))
        self.list_ranges.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list_ranges.itemChanged.connect(self.on_item_changed)
        self.list_ranges.currentItemChanged.connect(self.update_actions)
        self.list_ranges.itemSelectionChanged.connect(self.update_actions)
        self.list_ranges.itemDoubleClicked.connect(lambda item, column: self.edit_requested.emit(item.data(0, Qt.ItemDataRole.UserRole)))
        self.list_ranges.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_ranges.customContextMenuRequested.connect(self.context_menu)
        layout.addWidget(self.list_ranges, 1)
        bulk = QHBoxLayout()
        self.enable_selected = QPushButton(tr('Включить'))
        self.disable_selected = QPushButton(tr('Выключить'))
        for button, enabled in ((self.enable_selected, True), (self.disable_selected, False)):
            button.setToolTip(tr('Применить к выделению Shift / Ctrl; скрытые поиском сети не меняются'))
            button.clicked.connect(lambda checked=False, value=enabled: self.toggle_selected(value))
            bulk.addWidget(button)
        layout.addLayout(bulk)
        self.empty = QLabel()
        self.empty.setObjectName("Muted")
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty)
        self.summary = QLabel()
        self.summary.setObjectName("SelectionCount")
        layout.addWidget(self.summary)
        self.hint = QLabel()
        self.hint.setObjectName("Muted")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        add_row = QHBoxLayout()
        add = QPushButton(tr('Добавить сеть'))
        add.setProperty("primary", True)
        add.clicked.connect(self.add_requested)
        self.add_folder = QPushButton(tr('Папка'))
        self.add_folder.setToolTip(tr('Создать папку внутри выбранной папки или рядом с сетью'))
        self.add_folder.clicked.connect(self.folder_requested)
        add_row.addWidget(add, 1)
        add_row.addWidget(self.add_folder)
        layout.addLayout(add_row)
        actions = QHBoxLayout()
        self.edit_button = QPushButton(tr('Изменить'))
        self.delete_button = QPushButton(tr('Удалить'))
        self.edit_button.clicked.connect(lambda: self.edit_requested.emit(self.current_index()))
        self.delete_button.clicked.connect(lambda: self.delete_requested.emit(self.current_index()))
        actions.addWidget(self.edit_button)
        actions.addWidget(self.delete_button)
        layout.addLayout(actions)
        self.move_button = QPushButton(tr('Переместить в папку…'))
        self.move_button.clicked.connect(self.move_selected)
        layout.addWidget(self.move_button)
        self.update_actions()

    def current_index(self):
        item = self.list_ranges.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item is not None else ()

    def parent_path(self):
        path = self.current_index()
        if not path:
            return ()
        return path if node_at(self.groups, path).get("type") == "folder" else path[:-1]

    def set_groups(self, groups, current=None):
        selected = {item.data(0, Qt.ItemDataRole.UserRole) for item in self.list_ranges.selectedItems()}
        expanded = {path for path, item in self.items.items() if item.isExpanded()}
        scroll = self.list_ranges.verticalScrollBar().value()
        preserve = current is None
        current = self.current_index() if current is None else (current,) if isinstance(current, int) else tuple(current)
        old_paths = set(self.items)
        self.groups = deepcopy(groups)
        self.list_ranges.blockSignals(True)
        self.list_ranges.clear()
        self.items = {}
        for path, node in walk_nodes(self.groups):
            folder = node.get("type") == "folder"
            if folder:
                count = sum(1 for _ in walk_networks([node]))
                text = f"{node['name']}  ({count})"
            else:
                ranges = node.get("ranges", [])
                detail = ranges[0] if len(ranges) == 1 else tr('Диапазонов: {p0}', p0=len(ranges))
                text = f"{node['name']} · {detail}"
            item = QTreeWidgetItem([text])
            item.setData(0, Qt.ItemDataRole.UserRole, path)
            item.setToolTip(0, "\n".join([node["name"], *node.get("ranges", [])]))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            state = folder_state(node) if folder else "on" if node.get("enabled", True) else "off"
            item.setCheckState(0, {"on": Qt.CheckState.Checked, "mixed": Qt.CheckState.PartiallyChecked,
                                   "off": Qt.CheckState.Unchecked}[state])
            icon = QStyle.StandardPixmap.SP_DirIcon if folder else QStyle.StandardPixmap.SP_ComputerIcon
            item.setIcon(0, self.style().standardIcon(icon))
            if path[:-1]:
                self.items[path[:-1]].addChild(item)
            else:
                self.list_ranges.addTopLevelItem(item)
            self.items[path] = item
            item.setExpanded(folder and (path in expanded or path not in old_paths))
        if current in self.items:
            self.list_ranges.setCurrentItem(self.items[current], 0, QItemSelectionModel.SelectionFlag.NoUpdate)
        for path in selected if preserve else {current}:
            if path in self.items:
                self.items[path].setSelected(True)
        self.list_ranges.blockSignals(False)
        networks = list(walk_networks(self.groups))
        enabled = sum(n.get("enabled", True) for _, n in networks)
        self.select_all.setEnabled(bool(networks))
        self.select_all.setCheckState(Qt.CheckState.Checked if networks and enabled == len(networks)
                                     else Qt.CheckState.PartiallyChecked if enabled else Qt.CheckState.Unchecked)
        try:
            total = len(expand_ranges(selected_ranges(self.groups)))
            self.summary.setText(tr('Сетей: {p0} из {p1} · IP: {p2:,}', p0=enabled, p1=len(networks), p2=total).replace(",", " "))
        except ValueError:
            self.summary.setText(tr('Сетей: {p0} из {p1} · Проверьте лимит IP', p0=enabled, p1=len(networks)))
        self.filter_groups()
        self.list_ranges.verticalScrollBar().setValue(scroll)
        self.update_actions()

    def filter_groups(self):
        query = self.search.text().strip().casefold()
        def visit(groups, parent=(), ancestor_matches=False):
            any_match = False
            for index, node in enumerate(groups):
                path = (*parent, index)
                own = query in " ".join([node["name"], *node.get("ranges", [])]).casefold()
                matches = own or ancestor_matches
                if node.get("type") == "folder":
                    matches = visit(node.get("children", []), path, matches) or matches
                    if query and matches:
                        self.items[path].setExpanded(True)
                self.items[path].setHidden(not matches)
                any_match |= matches
            return any_match
        visible = visit(self.groups)
        self.empty.setVisible(not visible)
        self.empty.setText(tr('Ничего не найдено. Измените поиск.') if self.groups else tr('Добавьте сеть или папку для площадки.'))
        self.hint.setText(tr('Галочка папки меняет все вложенные сети. Shift / Ctrl — выделение. Кнопки учитывают поиск.'))
        self.update_actions()

    def visible_item(self, item):
        while item:
            if item.isHidden():
                return False
            item = item.parent()
        return True

    def selected_paths(self):
        return [item.data(0, Qt.ItemDataRole.UserRole) for item in self.list_ranges.selectedItems()
                if self.visible_item(item)]

    def selected_network_paths(self):
        roots = self.selected_paths()
        return [path for path, _ in walk_networks(self.groups)
                if self.visible_item(self.items[path]) and any(path[:len(root)] == root for root in roots)]

    def update_actions(self, *args):
        item = self.list_ranges.currentItem()
        available = item is not None and self.visible_item(item)
        self.edit_button.setEnabled(available)
        self.delete_button.setEnabled(available)
        selected = len(self.selected_network_paths())
        self.enable_selected.setEnabled(bool(selected))
        self.disable_selected.setEnabled(bool(selected))
        self.enable_selected.setText(tr('Включить ({p0})', p0=selected) if selected else tr('Включить'))
        self.disable_selected.setText(tr('Выключить ({p0})', p0=selected) if selected else tr('Выключить'))
        self.move_button.setEnabled(bool(self.selected_paths()))

    def toggle_selected(self, checked):
        candidate = deepcopy(self.groups)
        for path in self.selected_network_paths():
            node_at(candidate, path)["enabled"] = checked
        self.changed.emit(candidate)

    def on_item_changed(self, item, column):
        candidate = deepcopy(self.groups)
        set_enabled(node_at(candidate, item.data(0, Qt.ItemDataRole.UserRole)), item.checkState(0) == Qt.CheckState.Checked)
        self.changed.emit(candidate)

    def toggle_all(self, checked):
        candidate = deepcopy(self.groups)
        for _, node in walk_networks(candidate):
            node["enabled"] = checked
        self.changed.emit(candidate)

    def move_selected(self):
        paths = self.selected_paths()
        if not paths:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(tr('Переместить сети и папки'))
        dialog.setMinimumWidth(360)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(tr('Папка назначения')))
        combo = QComboBox()
        combo.addItem(tr('Все сети (корень)'), ())
        for path, node in walk_nodes(self.groups):
            if node.get("type") == "folder" and not any(path[:len(p)] == p for p in paths):
                label = " / ".join(node_at(self.groups, path[:i])["name"] for i in range(1, len(path)+1))
                combo.addItem(label, path)
        layout.addWidget(combo)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr('Переместить'))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr('Отмена'))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec():
            try:
                candidate = move_nodes(self.groups, paths, combo.currentData())
            except ValueError as exc:
                QMessageBox.warning(self, tr('Перемещение'), str(exc))
                return
            self.changed.emit(candidate)
            if self.groups == candidate:
                # Paths change on a move; don't select an unrelated former row.
                self.list_ranges.clearSelection()
                self.list_ranges.setCurrentItem(None)

    def context_menu(self, position):
        item = self.list_ranges.itemAt(position)
        if item and item not in self.list_ranges.selectedItems():
            self.list_ranges.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction(tr('Добавить сеть'), self.add_requested.emit)
        menu.addAction(tr('Создать папку'), self.folder_requested.emit)
        menu.addSeparator()
        menu.addAction(tr('Изменить'), lambda: self.edit_requested.emit(self.current_index())).setEnabled(self.edit_button.isEnabled())
        menu.addAction(tr('Переместить в папку…'), self.move_selected).setEnabled(self.move_button.isEnabled())
        menu.addAction(tr('Удалить'), lambda: self.delete_requested.emit(self.current_index())).setEnabled(self.delete_button.isEnabled())
        menu.exec(self.list_ranges.viewport().mapToGlobal(position))
