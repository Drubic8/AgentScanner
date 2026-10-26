"""Read-only batch preview and explicit, per-device command outcomes."""
from copy import deepcopy
from dataclasses import dataclass
from html import escape
from ipaddress import IPv4Address

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QDialog,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QSplitter, QTableView, QVBoxLayout)

from .event_log import redact
from .i18n import action_label, tr, tr_error


def compatibility_label(status):
    return {
        'checking': tr('Проверяется'), 'ready': tr('Готово'),
        'unsupported': tr('Не поддерживается'), 'offline': tr('Нет связи'),
        'auth_required': tr('Нужен доступ'), 'changed': tr('Данные изменились'),
        'unknown': tr('Не определено'), 'cancelled': tr('Отменено'),
        'failed': tr('Ошибка проверки'),
    }.get(status, tr('Не определено'))


def result_label(status):
    return {
        'succeeded': tr('Подтверждено'), 'unconfirmed': tr('Не подтверждено'),
        'failed': tr('Ошибка'), 'unsupported': tr('Не поддерживается'),
        'skipped': tr('Пропущено'), 'cancelled': tr('Отменено'),
    }.get(status, tr('Не определено'))


@dataclass
class BatchEntry:
    target: dict
    preflight_status: str = 'checking'
    preflight_message: str = ''
    attempted: bool = False
    dispatched: bool = False
    accepted: bool = False
    result_status: str = ''
    result_message: str = ''
    result: object = None

    @property
    def ip(self):
        return str(self.target['IP'])

    @property
    def issue(self):
        return (self.result_status not in ('', 'succeeded')
                or self.preflight_status not in ('checking', 'ready'))

    @property
    def retryable(self):
        if self.result is None:
            return False
        from miner_scanner.preflight import is_retry_safe
        return is_retry_safe(self.result)


class CommandBatchModel(QAbstractTableModel):
    def __init__(self, targets, parent=None):
        super().__init__(parent)
        self.entries = [BatchEntry(deepcopy(row)) for row in {row['IP']: row for row in targets}.values()]
        self.by_ip = {entry.ip: index for index, entry in enumerate(self.entries)}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.entries)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 7

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return (tr('IP'), tr('Модель'), tr('Прошивка'), tr('Совместимость'),
                    tr('Принято API'), tr('Результат'), tr('Подробности'))[section]

    def values(self, entry):
        target = entry.target
        firmware = ' '.join(str(target.get(field) or '') for field in ('Firmware', 'FirmwareVersion')).strip()
        if entry.result_status:
            state = result_label(entry.result_status)
        elif entry.attempted:
            state = tr('Ожидает подтверждения') if entry.dispatched else tr('В очереди')
        else:
            state = tr('Не отправлялась')
        api = tr('Да') if entry.accepted else tr('Нет подтверждения') if entry.dispatched else '—'
        details = entry.result_message or entry.preflight_message
        return (redact(entry.ip), redact(target.get('Model') or '—'), redact(firmware or '—'),
                compatibility_label(entry.preflight_status), api, state, tr_error(details))

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.entries):
            return None
        entry = self.entries[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            if index.column() == 0:
                try:
                    return int(IPv4Address(entry.ip))
                except ValueError:
                    return 0
            return self.values(entry)[index.column()].casefold()
        if role == Qt.ItemDataRole.DisplayRole:
            return self.values(entry)[index.column()].replace('\n', ' ⏎ ')
        if role == Qt.ItemDataRole.ToolTipRole:
            return '<pre>' + escape('\n'.join(self.values(entry))) + '</pre>'
        if role == Qt.ItemDataRole.ForegroundRole and index.column() in (3, 5):
            dark = QApplication.palette().color(QApplication.palette().ColorRole.Window).lightness() < 128
            status = entry.preflight_status if index.column() == 3 else entry.result_status
            if status in ('ready', 'succeeded'):
                return QColor('#69d7ad' if dark else '#147652')
            if status in ('failed', 'changed'):
                return QColor('#ff919b' if dark else '#b93649')
            if status in ('offline', 'auth_required', 'unsupported', 'unknown', 'unconfirmed', 'skipped'):
                return QColor('#eebc65' if dark else '#93600d')

    def update(self, ip, **values):
        row = self.by_ip.get(str(ip))
        if row is None:
            return
        entry = self.entries[row]
        for key, value in values.items():
            setattr(entry, key, value)
        self.dataChanged.emit(self.index(row, 0), self.index(row, 6))


class CommandBatchFilter(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.query = ''
        self.issues_only = False

    def filterAcceptsRow(self, row, parent):
        entry = self.sourceModel().entries[row]
        haystack = ' '.join(self.sourceModel().values(entry)).casefold()
        return (not self.issues_only or entry.issue) and all(part in haystack for part in self.query.split())


class CommandBatchDialog(QDialog):
    start_requested = pyqtSignal(list)
    retry_requested = pyqtSignal(list)
    cancel_requested = pyqtSignal()

    def __init__(self, targets, action, parent=None):
        super().__init__(parent)
        self.action = action
        self.phase = 'checking'
        self._stop_requested = False
        self._retry_pending = False
        self.setWindowTitle(tr('Массовая команда: {p0}', p0=action_label(action)))
        self.resize(1180, 730)
        self.setMinimumSize(900, 570)
        self.model = CommandBatchModel(targets, self)
        self.proxy = CommandBatchFilter(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(Qt.ItemDataRole.UserRole)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(10)
        title = QLabel(action_label(action))
        title.setObjectName('DialogTitle')
        title.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(title)
        self.hint = QLabel(tr('Сначала проверяется доступ и совместимость. Отправка начнётся после вашего подтверждения.'))
        self.hint.setObjectName('Muted')
        self.hint.setWordWrap(True)
        root.addWidget(self.hint)
        self.consequences = QLabel(tr('\nДля Avalon возобновление выполняется через перезагрузку.').strip())
        self.consequences.setWordWrap(True)
        self.consequences.setVisible(action in ('wakeup', 'normal', 'mining_start') and any(
            entry.target.get('ProfileId') == 'canaan.avalon' for entry in self.model.entries))
        root.addWidget(self.consequences)
        self.summary = QLabel()
        self.summary.setObjectName('SelectionCount')
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)
        self.delivery_summary = QLabel()
        self.delivery_summary.setObjectName('Muted')
        self.delivery_summary.setWordWrap(True)
        root.addWidget(self.delivery_summary)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr('Поиск по IP, модели или результату…'))
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName(tr('Поиск результатов команды'))
        self.search.textChanged.connect(self._filter_changed)
        self.issues_only = QCheckBox(tr('Только проблемы'))
        self.issues_only.toggled.connect(self._filter_changed)
        filters.addWidget(self.search, 1)
        filters.addWidget(self.issues_only)
        root.addLayout(filters)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setShowGrid(False)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.table.verticalHeader().hide()
        self.table.ensurePolished()
        self.table.verticalHeader().setDefaultSectionSize(max(34, self.table.fontMetrics().height() + 10))
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for column, width in enumerate((118, 170, 160, 145, 120, 165, 280)):
            self.table.setColumnWidth(column, width)
        self.table.selectionModel().selectionChanged.connect(self._update_details)
        splitter.addWidget(self.table)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(100)
        self.details.setAccessibleName(tr('Подробности результата команды'))
        self.details.setPlainText(tr('Выберите устройство, чтобы увидеть полный результат проверки и команды.'))
        splitter.addWidget(self.details)
        splitter.setSizes([390, 140])
        root.addWidget(splitter, 1)
        self.note = QLabel(tr('Положительный ответ API и подтверждённое состояние — разные результаты. Неподтверждённые команды автоматически не повторяются.'))
        self.note.setObjectName('Muted')
        self.note.setWordWrap(True)
        root.addWidget(self.note)
        buttons = QHBoxLayout()
        self.phase_label = QLabel()
        self.phase_label.setWordWrap(True)
        buttons.addWidget(self.phase_label, 1)
        self.start_button = QPushButton()
        self.start_button.setProperty('primary', True)
        self.start_button.clicked.connect(self._request_start)
        self.retry_button = QPushButton(tr('Повторить неудачные'))
        self.retry_button.setToolTip(tr('Повторная проверка команд, для которых известно, что запись не выполнялась. Неподтверждённые результаты исключены.'))
        self.retry_button.clicked.connect(self._request_retry)
        self.cancel_button = QPushButton(tr('Остановить'))
        self.cancel_button.clicked.connect(self._request_stop)
        self.close_button = QPushButton(tr('Закрыть'))
        self.close_button.clicked.connect(self.reject)
        for button in (self.start_button, self.retry_button, self.cancel_button, self.close_button):
            buttons.addWidget(button)
        root.addLayout(buttons)
        self.model.dataChanged.connect(self._refresh)
        self._refresh()

    def ready_targets(self):
        return [deepcopy(entry.target) for entry in self.model.entries if entry.preflight_status == 'ready']

    def retry_targets(self):
        return [deepcopy(entry.target) for entry in self.model.entries if entry.retryable]

    @property
    def active(self):
        return self.phase in ('checking', 'executing')

    def add_preflight(self, item):
        if self.phase != 'checking':
            return
        self.model.update(item.ip, preflight_status=item.status, preflight_message=redact(item.message))

    def finish_preflight(self):
        if self.phase != 'checking':
            return
        for entry in self.model.entries:
            if entry.preflight_status == 'checking':
                self.model.update(entry.ip, preflight_status='cancelled' if self._stop_requested else 'failed',
                    preflight_message=tr('Проверка остановлена.') if self._stop_requested else tr('Результат проверки не получен.'))
        self.phase = 'finished' if self._stop_requested else 'prepared'
        self._refresh()

    def begin_execution(self, targets=None):
        allowed = {row['IP'] for row in self.ready_targets()}
        chosen = allowed if targets is None else {row['IP'] for row in targets} & allowed
        if not chosen:
            return False
        self.phase = 'executing'
        self._stop_requested = False
        self._retry_pending = False
        for entry in self.model.entries:
            if entry.ip in chosen:
                self.model.update(entry.ip, attempted=True, dispatched=False, accepted=False,
                                  result_status='', result_message='', result=None)
        self._refresh()
        return True

    def mark_dispatched(self, ip, accepted):
        row = self.model.by_ip.get(str(ip))
        if row is not None and self.model.entries[row].attempted:
            self.model.update(ip, dispatched=True, accepted=bool(accepted))

    def set_result(self, ip, result):
        from dataclasses import replace
        sanitized = replace(result, message=redact(result.message))
        self.model.update(ip, attempted=True, accepted=bool(result.accepted_by_api),
                          result_status=result.status, result_message=sanitized.message, result=sanitized)

    def finish_execution(self):
        if self.phase != 'executing':
            return
        for entry in self.model.entries:
            if entry.attempted and not entry.result_status:
                self.model.update(entry.ip, result_status='unconfirmed',
                    result_message=tr('Результат не получен. Проверьте устройство перед повторной командой.'))
        self.phase = 'finished'
        self._refresh()

    def _request_start(self):
        if self.phase != 'prepared' or self._stop_requested:
            return
        targets = self.ready_targets()
        if self.begin_execution(targets):
            self.start_requested.emit(targets)

    def _request_retry(self):
        if self.phase == 'finished' and not self._retry_pending:
            targets = self.retry_targets()
            if targets:
                self._retry_pending = True
                self.retry_button.setEnabled(False)
                self.retry_requested.emit(targets)

    def _request_stop(self):
        if self.phase in ('checking', 'executing') and not self._stop_requested:
            self._stop_requested = True
            self.cancel_requested.emit()
            self._refresh()

    def _filter_changed(self):
        self.proxy.query = self.search.text().casefold()
        self.proxy.issues_only = self.issues_only.isChecked()
        self.proxy.invalidateFilter()

    def _update_details(self):
        selected = self.table.selectionModel().selectedRows()
        if selected:
            entry = self.model.entries[self.proxy.mapToSource(selected[0]).row()]
            values = self.model.values(entry)
            lines = [f"{tr('IP')}: {values[0]} · {tr('Модель')}: {values[1]}"]
            if entry.attempted:
                lines.extend((f"{tr('Результат')}: {values[5]} · {tr('Принято API')}: {values[4]}",
                              tr_error(entry.result_message)))
            lines.extend((f"{tr('Совместимость')}: {values[3]}", tr_error(entry.preflight_message),
                          f"{tr('Прошивка')}: {values[2]}"))
            self.details.setPlainText('\n'.join(line for line in lines if line))
        else:
            self.details.setPlainText(tr('Выберите устройство, чтобы увидеть полный результат проверки и команды.'))

    def _refresh(self, *args):
        entries = self.model.entries
        total = len(entries)
        checked = sum(entry.preflight_status != 'checking' for entry in entries)
        ready = sum(entry.preflight_status == 'ready' for entry in entries)
        self.summary.setText(tr('Выбрано: {p0} · Проверено: {p1}/{p0} · Готово: {p2} · Не готовы: {p3}',
            p0=total, p1=checked, p2=ready, p3=checked - ready))
        attempted = sum(entry.attempted for entry in entries)
        self.delivery_summary.setText(tr('Этап отправки: {p0}/{p1} · Принято API: {p2} · Подтверждено: {p3} · Итогов: {p4}/{p1}',
            p0=sum(entry.dispatched for entry in entries), p1=attempted,
            p2=sum(entry.accepted for entry in entries), p3=sum(entry.result_status == 'succeeded' for entry in entries),
            p4=sum(bool(entry.result_status) for entry in entries)))
        active = self.active
        self.hint.setText(tr('Команды отправляются готовым устройствам. Проверка состояния выполняется отдельно.')
            if self.phase == 'executing' else tr('Результаты сохранены в этом окне. Выберите устройство для подробностей.')
            if self.phase == 'finished' else tr('Сначала проверяется доступ и совместимость. Отправка начнётся после вашего подтверждения.'))
        self.start_button.setText(tr('Отправить для {p0}', p0=ready))
        self.start_button.setVisible(self.phase in ('checking', 'prepared'))
        self.start_button.setEnabled(self.phase == 'prepared' and ready > 0 and not self._stop_requested)
        self.retry_button.setVisible(self.phase == 'finished')
        self.retry_button.setEnabled(any(entry.retryable for entry in entries) and not self._retry_pending)
        self.cancel_button.setVisible(active)
        self.cancel_button.setEnabled(not self._stop_requested)
        self.close_button.setEnabled(not active)
        self.phase_label.setText(tr('Останавливаем…') if self._stop_requested and active else {
            'checking': tr('Проверка совместимости…'), 'prepared': tr('Проверьте список перед отправкой.'),
            'executing': tr('Команда выполняется…'), 'finished': tr('Операция завершена.'),
        }[self.phase])
        self._update_details()

    def reject(self):
        if self.phase in ('checking', 'executing'):
            self._request_stop()
            return
        super().reject()

    def closeEvent(self, event):
        if self.phase in ('checking', 'executing'):
            self._request_stop()
            event.ignore()
        else:
            super().closeEvent(event)
