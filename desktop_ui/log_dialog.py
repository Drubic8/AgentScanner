"""Operational event journal: buffered updates, filters, selection and scoped export."""
from collections import Counter
from pathlib import Path
from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QKeySequence
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox,
    QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu,
    QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTableView, QVBoxLayout)
from .event_log import LogEvent, OUTCOMES, export_events
from .i18n import tr

# This module is fully bilingual; raw diagnostic messages remain in their original language.
TEXT = {
    "title": ("Журнал событий", "Event journal"),
    "hint": ("Результаты сканирования и команд. Подробности выбранного события — внизу.",
             "Scan and command results. Select an event to see its full details below."),
    "search": ("Поиск: IP, команда, текст…", "Search: IP, action, message…"),
    "all_levels": ("Все уровни", "All levels"), "all_results": ("Все результаты", "All results"),
    "all_actions": ("Все команды", "All actions"), "follow": ("Следить за новыми", "Follow new events"),
    "reset": ("Сбросить фильтры", "Reset filters"), "copy": ("Копировать", "Copy"),
    "selected": ("Выбранные события", "Selected events"), "visible": ("По фильтру", "Filtered events"),
    "all": ("Весь журнал", "Entire journal"), "export": ("Экспорт…", "Export…"),
    "clear": ("Очистить", "Clear"), "empty": ("Пока нет событий. Начните сканирование или отправьте команду.",
                                               "No events yet. Start a scan or send a command."),
    "no_match": ("Нет совпадений. Измените запрос или сбросьте фильтры.",
                 "No matching events. Change your search or reset the filters."),
    "details": ("Выберите событие для просмотра подробностей.", "Select an event to view its details."),
    "counts": ("Показано {visible} / {total} · Ошибки: {errors} · Предупреждения: {warnings}",
               "Showing {visible} / {total} · Errors: {errors} · Warnings: {warnings}"),
    "retained": ("В памяти — последние {limit:,} событий; удалено старых: {dropped}. Экспортируйте нужные события перед закрытием.",
                 "Memory holds the latest {limit:,} events; older events removed: {dropped}. Export before closing."),
    "saved": ("Сохранено событий: {count} · {path}", "Saved {count} events · {path}"),
    "copied": ("Скопировано событий: {count}", "Copied {count} events"),
    "failed": ("Не удалось сохранить журнал:\n{error}", "Could not save the journal:\n{error}"),
    "clear_title": ("Очистка журнала", "Clear journal"),
    "clear_question": ("Удалить все события из памяти? Сохранённые файлы останутся.",
                       "Remove all events from memory? Exported files will remain."),
}
LEVELS = {"info": ("Информация", "Info"), "success": ("Успех", "Success"),
          "warning": ("Предупреждение", "Warning"), "error": ("Ошибка", "Error")}
RESULTS = {"succeeded": ("Подтверждено", "Confirmed"), "unconfirmed": ("Не подтверждено", "Unconfirmed"),
           "failed": ("Ошибка", "Failed"), "unsupported": ("Не поддерживается", "Unsupported"),
           "skipped": ("Пропущено", "Skipped")}


class EventModel(QAbstractTableModel):
    def __init__(self, parent=None, limit=5000):
        super().__init__(parent)
        self.events = []
        self.limit = limit
        self.dropped = 0
        self.language = "ru"

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.events)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 6

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return (("Время", "Time"), ("Уровень", "Level"), ("IP", "IP"),
                    ("Команда", "Action"), ("Результат", "Result"), ("Сообщение", "Message"))[section][self.language == "en"]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        event = self.events[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            english = self.language == "en"
            return (event.timestamp[11:23], LEVELS[event.level][english], event.ip or "—", event.action or "—",
                    RESULTS[event.outcome][english] if event.outcome else "—", event.message.replace("\n", " ⏎ "))[index.column()]
        if role == Qt.ItemDataRole.ToolTipRole:
            # Qt tooltips can interpret HTML; escape even though table/detail data is plain text.
            from html import escape
            return "<pre>" + escape(event.line()) + "</pre>"
        if role == Qt.ItemDataRole.ForegroundRole and index.column() in (1, 4):
            dark = QApplication.palette().color(QApplication.palette().ColorRole.Window).lightness() < 128
            colors = {"error": ("#b93649", "#ff919b"), "warning": ("#93600d", "#eebc65"),
                      "success": ("#147652", "#69d7ad")}
            if event.level in colors:
                return QColor(colors[event.level][dark])

    def append(self, events):
        if not events:
            return
        if len(events) > self.limit:
            self.dropped += len(events) - self.limit
            events = events[-self.limit:]
        remove = max(0, len(self.events) + len(events) - self.limit)
        if remove:
            self.beginRemoveRows(QModelIndex(), 0, remove - 1)
            del self.events[:remove]
            self.dropped += remove
            self.endRemoveRows()
        start = len(self.events)
        self.beginInsertRows(QModelIndex(), start, start + len(events) - 1)
        self.events.extend(events)
        self.endInsertRows()

    def clear(self):
        self.beginResetModel()
        self.events.clear()
        self.dropped = 0
        self.endResetModel()


class EventFilter(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.query = self.level = self.outcome = self.action = ""

    def filterAcceptsRow(self, row, parent):
        return self.sourceModel().events[row].matches(self.query, self.level, self.outcome, self.action)


class LogDialog(QDialog):
    language_changed = pyqtSignal(str)

    def __init__(self, parent=None, *, language="ru"):
        super().__init__(parent)
        self.resize(1120, 720)
        self.setMinimumSize(900, 560)
        self.language = language if language in ("ru", "en") else "ru"
        self.model = EventModel(self)
        self.proxy = EventFilter(self)
        self.proxy.setSourceModel(self.model)
        self.pending = []
        self.flush_timer = QTimer(self)
        self.flush_timer.setSingleShot(True)
        self.flush_timer.setInterval(75)
        self.flush_timer.timeout.connect(self.flush)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(8)
        heading = QHBoxLayout()
        self.title = QLabel()
        self.title.setObjectName("DialogTitle")
        heading.addWidget(self.title, 1)
        self.language_box = QComboBox()
        self.language_box.addItem("Русский", "ru")
        self.language_box.addItem("English", "en")
        self.language_box.setAccessibleName(tr("Язык интерфейса"))
        self.language_box.setCurrentIndex(self.language_box.findData(self.language))
        self.language_box.currentIndexChanged.connect(lambda: self.set_language(self.language_box.currentData()))
        heading.addWidget(self.language_box)
        root.addLayout(heading)
        self.hint = QLabel()
        self.hint.setObjectName("Muted")
        self.hint.setWordWrap(True)
        root.addWidget(self.hint)
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.apply_filter)
        self.level = QComboBox()
        self.outcome = QComboBox()
        self.action = QComboBox()
        for combo in (self.level, self.outcome, self.action):
            combo.currentIndexChanged.connect(self.apply_filter)
        row.addWidget(self.search, 1)
        row.addWidget(self.level)
        row.addWidget(self.outcome)
        row.addWidget(self.action)
        root.addLayout(row)
        options = QHBoxLayout()
        self.follow = QCheckBox()
        self.follow.setChecked(True)
        self.follow.toggled.connect(lambda enabled: self.table.scrollToBottom() if enabled else None)
        self.reset = QPushButton()
        self.reset.clicked.connect(self.reset_filters)
        options.addWidget(self.follow)
        options.addStretch()
        options.addWidget(self.reset)
        root.addLayout(options)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        for col, width in enumerate((110, 142, 118, 90, 155, 370)):
            self.table.setColumnWidth(col, width)
        self.table.selectionModel().selectionChanged.connect(self.update_detail)
        self.splitter.addWidget(self.table)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMaximumBlockCount(1000)
        self.detail.setMinimumHeight(80)
        self.splitter.addWidget(self.detail)
        self.splitter.setSizes([420, 100])
        root.addWidget(self.splitter, 1)
        self.empty = QLabel()
        self.empty.setObjectName("Muted")
        self.empty.setWordWrap(True)
        root.addWidget(self.empty)
        self.counts = QLabel()
        self.counts.setObjectName("SelectionCount")
        root.addWidget(self.counts)
        self.retention = QLabel()
        self.retention.setObjectName("Muted")
        self.retention.setWordWrap(True)
        root.addWidget(self.retention)
        buttons = QHBoxLayout()
        self.copy_button = QPushButton()
        copy_menu = QMenu(self)
        self.copy_selected = copy_menu.addAction("", lambda: self.copy_events(selected=True))
        self.copy_visible = copy_menu.addAction("", self.copy_events)
        self.copy_button.setMenu(copy_menu)
        self.scope = QComboBox()
        self.export_button = QPushButton()
        self.export_button.setProperty("primary", True)
        self.export_button.clicked.connect(self.save_log)
        self.clear_button = QPushButton()
        self.clear_button.clicked.connect(self.ask_clear)
        buttons.addWidget(self.copy_button)
        buttons.addStretch()
        buttons.addWidget(self.scope)
        buttons.addWidget(self.export_button)
        buttons.addWidget(self.clear_button)
        root.addLayout(buttons)
        self.feedback = QLabel()
        self.feedback.setObjectName("Muted")
        self.feedback.setWordWrap(True)
        self.feedback.hide()
        root.addWidget(self.feedback)
        self.scope.currentIndexChanged.connect(self.update_counts)
        copy = QAction(self)
        copy.setShortcut(QKeySequence.StandardKey.Copy)
        copy.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        copy.triggered.connect(lambda: self.copy_events(selected=True))
        self.table.addAction(copy)
        search = QAction(self)
        search.setShortcut(QKeySequence.StandardKey.Find)
        search.triggered.connect(self.search.setFocus)
        self.addAction(search)
        self.set_language(self.language, emit=False)

    def tr_text(self, key):
        return TEXT[key][self.language == "en"]

    def set_language(self, language, *, emit=True):
        self.language = language
        self.model.language = language
        self.language_box.setAccessibleName("Interface language" if language == "en" else "Язык интерфейса")
        self.setWindowTitle(self.tr_text("title") + " · ASIC Monitor")
        for widget, key in ((self.title, "title"), (self.hint, "hint"), (self.follow, "follow"),
                            (self.reset, "reset"), (self.copy_button, "copy"), (self.export_button, "export"),
                            (self.clear_button, "clear")):
            widget.setText(self.tr_text(key))
        self.search.setPlaceholderText(self.tr_text("search"))
        self.search.setAccessibleName(self.tr_text("search"))
        self.level.setAccessibleName(self.tr_text("all_levels"))
        self.outcome.setAccessibleName(self.tr_text("all_results"))
        self.action.setAccessibleName(self.tr_text("all_actions"))
        self.scope.setAccessibleName(self.tr_text("export"))
        self.detail.setPlaceholderText(self.tr_text("details"))
        self.copy_selected.setText(self.tr_text("selected") + " (Ctrl+C)")
        self.copy_visible.setText(self.tr_text("visible"))
        for combo, items in ((self.level, [(self.tr_text("all_levels"), "")] + [(v[language == "en"], k) for k, v in LEVELS.items()]),
                             (self.outcome, [(self.tr_text("all_results"), "")] + [(RESULTS[k][language == "en"], k) for k in OUTCOMES]),
                             (self.scope, [(self.tr_text("visible"), "visible"), (self.tr_text("all"), "all")])):
            value = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            for label, code in items:
                combo.addItem(label, code)
            combo.setCurrentIndex(max(0, combo.findData(value)))
            combo.blockSignals(False)
        self.update_actions()
        self.model.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, 5)
        if self.model.rowCount():
            self.model.dataChanged.emit(self.model.index(0, 0), self.model.index(self.model.rowCount() - 1, 5))
        self.update_counts()
        if emit:
            self.language_changed.emit(language)

    def append_log(self, text, *, action="", level=None):
        self.pending.append(LogEvent.create(text, action=action, level=level))
        if len(self.pending) > self.model.limit:
            self.pending.pop(0)
            self.model.dropped += 1
        if not self.flush_timer.isActive():
            self.flush_timer.start()

    def flush(self):
        self.flush_timer.stop()
        pending, self.pending = self.pending, []
        self.model.append(pending)
        self.update_actions()
        self.update_counts()
        if self.follow.isChecked():
            self.table.scrollToBottom()

    def update_actions(self):
        value = self.action.currentData()
        actions = sorted({event.action for event in self.model.events if event.action})
        if value and value not in actions:
            actions.append(value)
        self.action.blockSignals(True)
        self.action.clear()
        self.action.addItem(self.tr_text("all_actions"), "")
        for action in actions:
            self.action.addItem(action, action)
        self.action.setCurrentIndex(max(0, self.action.findData(value)))
        self.action.blockSignals(False)

    def apply_filter(self):
        self.proxy.query = self.search.text()
        self.proxy.level = self.level.currentData() or ""
        self.proxy.outcome = self.outcome.currentData() or ""
        self.proxy.action = self.action.currentData() or ""
        self.proxy.invalidateFilter()
        self.update_counts()

    def reset_filters(self):
        self.search.clear()
        for combo in (self.level, self.outcome, self.action):
            combo.setCurrentIndex(0)
        self.apply_filter()

    def update_counts(self):
        totals = Counter(event.level for event in self.model.events)
        visible, total = self.proxy.rowCount(), self.model.rowCount()
        self.counts.setText(self.tr_text("counts").format(visible=visible, total=total,
            errors=totals["error"], warnings=totals["warning"]))
        self.retention.setText(self.tr_text("retained").format(limit=self.model.limit, dropped=self.model.dropped))
        self.empty.setText(self.tr_text("no_match" if total else "empty"))
        self.empty.setVisible(not visible)
        self.export_button.setEnabled(bool(total if self.scope.currentData() == "all" else visible))
        self.copy_visible.setEnabled(bool(visible))
        self.clear_button.setEnabled(bool(total))
        self.update_detail()

    def update_detail(self):
        rows = self.table.selectionModel().selectedRows()
        self.copy_selected.setEnabled(bool(rows))
        current = self.table.currentIndex()
        self.detail.setPlainText(self.model.events[self.proxy.mapToSource(current).row()].line()
                                if rows and current.isValid() else "")

    def events_in_scope(self, *, selected=False, all_events=False):
        self.flush()
        if all_events:
            return list(self.model.events)
        rows = sorted(index.row() for index in self.table.selectionModel().selectedRows()) if selected else range(self.proxy.rowCount())
        return [self.model.events[self.proxy.mapToSource(self.proxy.index(row, 0)).row()] for row in rows]

    def copy_events(self, *, selected=False):
        events = self.events_in_scope(selected=selected)
        if events:
            QApplication.clipboard().setText(export_events(events, "txt"))
            self.show_feedback(self.tr_text("copied").format(count=len(events)))

    def save_log(self):
        events = self.events_in_scope(all_events=self.scope.currentData() == "all")
        if not events:
            return
        filters = "Text (*.txt);;CSV (*.csv);;JSON Lines (*.jsonl)"
        path, selected_filter = QFileDialog.getSaveFileName(self, self.tr_text("export"), "ASIC_Scanner_Log.txt", filters)
        if not path:
            return
        kind = Path(path).suffix.lower().lstrip(".")
        if kind not in ("txt", "csv", "jsonl"):
            kind = "csv" if "csv" in selected_filter else "jsonl" if "jsonl" in selected_filter else "txt"
            path += "." + kind
        try:
            Path(path).write_text(export_events(events, kind), encoding="utf-8-sig" if kind == "csv" else "utf-8", newline="")
            self.show_feedback(self.tr_text("saved").format(count=len(events), path=path))
        except OSError as exc:
            QMessageBox.warning(self, self.tr_text("export"), self.tr_text("failed").format(error=exc))

    def ask_clear(self):
        self.flush()
        if QMessageBox.question(self, self.tr_text("clear_title"), self.tr_text("clear_question"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.clear_log()

    def show_feedback(self, text):
        self.feedback.setText(text)
        self.feedback.setVisible(bool(text))

    def clear_log(self):
        self.pending.clear()
        self.flush_timer.stop()
        self.model.clear()
        self.detail.clear()
        self.feedback.clear()
        self.feedback.hide()
        self.update_actions()
        self.apply_filter()

    def showEvent(self, event):
        self.flush()
        super().showEvent(event)
