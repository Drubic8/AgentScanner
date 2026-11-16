"""Explicit pool changes: read-only review, then one write per selected ASIC.

The dialog keeps configuration plans in memory. Only reviewed, public change
fields reach the table; pool passwords are never copied to its model.
"""
from copy import deepcopy
from ipaddress import IPv4Address
import json
from threading import Event

from PyQt6.QtCore import (QAbstractTableModel, QIODevice, QModelIndex, QSaveFile,
    QSignalBlocker, Qt, QThread, pyqtSignal)
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
    QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLayout, QLineEdit,
    QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSpinBox, QTabWidget, QTableView, QVBoxLayout,
    QWidget)

from miner_scanner.models import CommandResult
from miner_scanner.pool_configuration import (PoolEdit, WorkerNaming,
    collect_pool_evidence_batch, execute_pool_batch, prepare_pool_batch)
from miner_scanner.pool_evidence import evidence_from_plan, evidence_report
from .event_log import redact
from .i18n import get_language, tr_error


def _t(ru, en):
    return en if get_language() == 'en' else ru


def _status(code):
    return {
        'selected': _t('Не проверено', 'Not checked'),
        'checking': _t('Проверяется', 'Checking'),
        'observed': _t('Сведения прочитаны', 'Evidence collected'),
        'ready': _t('Готово', 'Ready'),
        'unchanged': _t('Без изменений', 'Unchanged'),
        'unsupported': _t('Не поддерживается', 'Unsupported'),
        'auth_required': _t('Нужен доступ', 'Access required'),
        'offline': _t('Нет связи', 'Offline'),
        'changed': _t('Данные изменились', 'Configuration changed'),
        'failed': _t('Ошибка', 'Failed'),
        'cancelled': _t('Отменено', 'Cancelled'),
        'succeeded': _t('Подтверждено', 'Confirmed'),
        'unconfirmed': _t('Не подтверждено', 'Unconfirmed'),
        'skipped': _t('Пропущено', 'Skipped'),
        'executing': _t('Применяется', 'Applying'),
    }.get(code, _t('Не определено', 'Unknown'))


def _ordered_targets(targets):
    unique = {}
    for target in targets:
        row = deepcopy(target)
        ip = str(IPv4Address(row['IP']))
        if ip in unique and unique[ip].get('DeviceId') != row.get('DeviceId'):
            raise ValueError(_t('Одному IP соответствуют разные устройства; обновите выбор.',
                                'One IP identifies different devices; refresh the selection.'))
        row['IP'] = ip
        unique.setdefault(ip, row)
    return sorted(unique.values(), key=lambda row: int(IPv4Address(row['IP'])))


class PoolPreviewModel(QAbstractTableModel):
    """A virtualized, secret-free review with one row per edited field."""

    def __init__(self, targets, parent=None):
        super().__init__(parent)
        self.targets = {row['IP']: row for row in targets}
        self.rows = []
        self.outcomes = {}
        self.reset()

    def reset(self, status='selected'):
        self.beginResetModel()
        self.rows = [self._placeholder(row, status) for row in self.targets.values()]
        self.outcomes = {}
        self.endResetModel()

    @staticmethod
    def _placeholder(target, status, message=''):
        version = ' '.join(str(target.get(key) or '') for key in ('Firmware', 'FirmwareVersion')).strip()
        return [target['IP'], str(target.get('Model') or '—'), version or '—',
                '—', '—', '—', '—', status, '—', message]

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 10

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return (_t('IP', 'IP'), _t('Модель', 'Model'), _t('Прошивка / версия', 'Firmware / version'),
                _t('Пул', 'Pool'), _t('Поле', 'Field'), _t('Сейчас', 'Current'),
                _t('Будет', 'Proposed'), _t('Результат', 'Result'), _t('Принято API', 'API accepted'),
                _t('Подробности', 'Details'))[section]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        value = self.rows[index.row()][index.column()]
        if index.column() == 7:
            value = _status(value)
        elif index.column() == 9:
            value = tr_error(value)
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole):
            return redact(str(value)).replace('\n', ' ⏎ ')
        if role == Qt.ItemDataRole.ToolTipRole:
            # Plain table cells remain literal; no device-provided HTML is used.
            row = self.rows[index.row()]
            return redact('\n'.join(str(part) for part in (row[0], row[1], row[2],
                value, tr_error(row[9])) if part)).replace('<', '‹').replace('>', '›')

    def add_plan(self, plan):
        target = self.targets.get(plan.ip)
        if target is None:
            return
        rows = []
        for change in plan.changes:
            row = self._placeholder(target, plan.status, redact(plan.message))
            row[3] = str(change.slot + 1)
            row[4] = {'url': 'URL', 'user': 'Worker', 'pass': _t('Пароль', 'Password')}.get(change.field, '—')
            if change.field == 'pass':
                row[5], row[6] = _t('Скрыт', 'Hidden'), _t('Изменить', 'Change')
            else:
                row[5] = str(change.before) if change.before is not None else _t('Неизвестно', 'Unknown')
                row[6] = str(change.after) if change.after is not None else '—'
                if change.after == '':
                    row[6] = _t('(очистить)', '(clear)')
            rows.append(row)
        if not rows:
            rows = [self._placeholder(target, plan.status, redact(plan.message))]
        # A review can arrive in any order, while the numbering order stays fixed.
        self.beginResetModel()
        indexes = [index for index, row in enumerate(self.rows) if row[0] == plan.ip]
        position = indexes[0] if indexes else len(self.rows)
        self.rows = [row for row in self.rows if row[0] != plan.ip]
        self.rows[position:position] = rows
        self.endResetModel()

    def set_status(self, ip, status, message=''):
        for index, row in enumerate(self.rows):
            if row[0] == ip:
                row[7], row[9] = status, redact(message)
                self.dataChanged.emit(self.index(index, 7), self.index(index, 9))

    def set_result(self, ip, result):
        self.outcomes[ip] = (result.status, bool(result.accepted_by_api))
        self.set_status(ip, result.status, result.message)
        for index, row in enumerate(self.rows):
            if row[0] == ip:
                row[8] = _t('Да', 'Yes') if result.accepted_by_api else _t('Нет подтверждения', 'Not confirmed')
                self.dataChanged.emit(self.index(index, 8), self.index(index, 8))


class _PoolJob(QThread):
    plan_ready = pyqtSignal(object)
    result_ready = pyqtSignal(str, object)
    failed = pyqtSignal(str)

    def __init__(self, service, phase, targets=(), edits=(), plans=(), *, evidence_only=False, parent=None):
        super().__init__(parent)
        self.service, self.phase = service, phase
        self.targets, self.edits, self.plans = targets, edits, plans
        self.evidence_only = evidence_only
        self.cancel = Event()

    def run(self):
        try:
            if self.phase == 'checking':
                iterator = (collect_pool_evidence_batch(self.service, self.targets, cancel=self.cancel, workers=8)
                    if self.evidence_only else prepare_pool_batch(self.service, self.targets, self.edits,
                                                                 cancel=self.cancel, workers=8))
                for plan in iterator:
                    self.plan_ready.emit(plan)
            else:
                for ip, result in execute_pool_batch(self.service, self.plans,
                                                    cancel=self.cancel, workers=8):
                    self.result_ready.emit(ip, result)
        except ValueError as exc:
            # Top-level batch validation has fixed local messages, without values.
            self.failed.emit(str(exc))
        except Exception:
            # Exceptions can contain transport request data: keep them out of UI/logs.
            self.failed.emit('')


class _SlotEditor(QWidget):
    changed = pyqtSignal()

    def __init__(self, slot, parent=None):
        super().__init__(parent)
        self.slot = slot
        form = QFormLayout(self)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.url_mode = self._mode()
        self.url = QLineEdit()
        self.url.setAccessibleName(_t(f'URL пула {slot + 1}', f'Pool {slot + 1} URL'))
        self.url.setPlaceholderText('stratum+tcp://pool.example:3333')
        self.url.setEnabled(False)
        url_row = QHBoxLayout()
        url_row.addWidget(self.url_mode)
        url_row.addWidget(self.url, 1)
        form.addRow('URL', url_row)
        self.worker = QCheckBox(_t('Использовать общее имя и индивидуальную часть ниже',
                                   'Use the common name and individual suffix below'))
        self.worker.setAccessibleName(_t(f'Изменить worker пула {slot + 1}', f'Change pool {slot + 1} worker'))
        form.addRow('Worker', self.worker)
        self.password_mode = self._mode()
        self.password_mode.setAccessibleName(_t(f'Действие для пароля пула {slot + 1}', f'Pool {slot + 1} password action'))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setAccessibleName(_t(f'Новый пароль пула {slot + 1}', f'New pool {slot + 1} password'))
        self.password.setToolTip(_t('Пароль для подключения к пулу; он передаётся как поле настроек майнинга. Пароль доступа к ASIC задаётся отдельно.',
                                    'The password for connecting to the pool is sent as a mining settings field. Configure the ASIC access password separately.'))
        self.password.setEnabled(False)
        password_row = QHBoxLayout()
        password_row.addWidget(self.password_mode)
        password_row.addWidget(self.password, 1)
        form.addRow(_t('Пароль пула', 'Pool password'), password_row)
        note = QLabel(_t('«Не менять» сохраняет значение каждого ASIC. «Очистить» удаляет только выбранное поле.',
                         'Keep preserves each ASIC’s value. Clear removes only the selected field.'))
        note.setObjectName('Muted')
        note.setWordWrap(True)
        form.addRow(note)
        self.url_mode.currentIndexChanged.connect(self._changed)
        self.password_mode.currentIndexChanged.connect(self._changed)
        self.url.textChanged.connect(self.changed)
        self.password.textChanged.connect(self.changed)
        self.worker.toggled.connect(self.changed)

    @staticmethod
    def _mode():
        widget = QComboBox()
        for ru, en in (('Не менять', 'Keep'), ('Задать', 'Set'), ('Очистить', 'Clear')):
            widget.addItem(_t(ru, en))
        return widget

    def _changed(self):
        self.url.setEnabled(self.url_mode.currentIndex() == 1)
        self.password.setEnabled(self.password_mode.currentIndex() == 1)
        self.changed.emit()


class PoolSettingsDialog(QDialog):
    completed = pyqtSignal()

    def __init__(self, service, targets, parent=None):
        super().__init__(parent)
        self.service = service
        self.targets = _ordered_targets(targets)
        self.plans = {}
        self.results = {}
        self._evidence_by_ip = {}
        self._read_only_evidence = False
        self.phase = 'editing'
        self._job = None
        self._stop_requested = False
        self._failed = False
        self._attempted = set()
        self._checked = 0
        self._building = True
        self.setWindowTitle(_t('Настройка пулов и workers', 'Pool and worker settings'))
        self.resize(1160, 790)
        self.setMinimumSize(820, 620)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(10)
        title = QLabel(_t('Пулы и workers', 'Pools and workers'))
        title.setObjectName('DialogTitle')
        title_row = QHBoxLayout()
        title_row.addWidget(title, 1)
        self.evidence_button = QPushButton(_t('Прочитать сведения о совместимости', 'Read compatibility evidence'))
        self.evidence_button.setToolTip(_t(
            'Только чтение интерфейса и структуры настроек. Выбирать поля для изменения не требуется; команды записи не отправляются.',
            'Read only the interface and settings structure. No fields need to be selected for editing; no write commands are sent.'))
        self.evidence_button.clicked.connect(self.start_evidence)
        title_row.addWidget(self.evidence_button)
        root.addLayout(title_row)
        hint = QLabel(_t('Меняются только выбранные поля. Пароль пула — настройка майнинга; доступ к ASIC задаётся отдельно.',
                         'Only selected fields change. The pool password is a mining setting; configure ASIC access separately.'))
        hint.setWordWrap(True)
        hint.setObjectName('Muted')
        root.addWidget(hint)
        self.settings_toggle = QPushButton(_t('Скрыть параметры изменения', 'Hide change settings'))
        self.settings_toggle.setCheckable(True)
        self.settings_toggle.setChecked(True)
        self.settings_toggle.toggled.connect(self._toggle_settings)
        tools_row = QHBoxLayout()
        tools_row.addWidget(self.settings_toggle)
        tools_row.addStretch(1)
        self.export_button = QPushButton(_t('Сохранить сведения о совместимости…', 'Save compatibility evidence…'))
        self.export_button.setToolTip(_t(
            'Сохранить уже полученные технические сведения без адресов устройств, имён workers, паролей и токенов. Дополнительных запросов к ASIC нет.',
            'Save the technical evidence already collected, excluding device addresses, worker names, passwords and tokens. No additional ASIC requests.'))
        self.export_button.clicked.connect(self.export_evidence)
        tools_row.addWidget(self.export_button)
        root.addLayout(tools_row)
        self.settings = QWidget()
        settings_layout = QVBoxLayout(self.settings)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.slots = []
        for slot in range(3):
            editor = _SlotEditor(slot)
            self.slots.append(editor)
            self.tabs.addTab(editor, _t(f'Пул {slot + 1}', f'Pool {slot + 1}'))
            editor.changed.connect(self._form_changed)
        self.tabs.ensurePolished()
        self.tabs.setMinimumHeight(self.tabs.minimumSizeHint().height())
        settings_layout.addWidget(self.tabs)
        self.whatsminer_password_hint = QLabel(_t(
            'WhatsMiner RPC 3 не возвращает пароли пулов. Для записи задайте пароль каждого из трёх слотов, даже если URL и worker не меняются. «Очистить» явно задаёт пустой пароль; автоматической подстановки нет.',
            'WhatsMiner RPC 3 does not return pool passwords. To write settings, explicitly set the password of all three slots, even if the URL and worker stay unchanged. Clear explicitly sets an empty password; no default is substituted.'))
        self.whatsminer_password_hint.setWordWrap(True)
        self.whatsminer_password_hint.setObjectName('Muted')
        self.whatsminer_password_hint.setVisible(any(self._is_whatsminer(row) for row in self.targets))
        settings_layout.addWidget(self.whatsminer_password_hint)
        naming_box = QGroupBox(_t('Worker: общее имя.индивидуальная часть', 'Worker: common name.individual suffix'))
        naming_group_layout = QVBoxLayout(naming_box)
        self.keep_worker_name = QCheckBox(_t('Сохранить общее имя до точки', 'Keep the common name before the dot'))
        naming_group_layout.addWidget(self.keep_worker_name)
        naming_layout = QHBoxLayout()
        name_label = QLabel(_t('Имя до точки', 'Name before dot'))
        self.worker_name = QLineEdit()
        self.worker_name.setPlaceholderText('TEST')
        self.worker_name.setAccessibleName(name_label.text())
        name_label.setBuddy(self.worker_name)
        naming_layout.addWidget(name_label)
        naming_layout.addWidget(self.worker_name, 2)
        self.naming_mode = QComboBox()
        self.naming_mode.addItem(_t('По порядку', 'Sequential'), 'index')
        self.naming_mode.addItem(_t('Полный IP', 'Full IP'), 'ip')
        self.naming_mode.addItem(_t('Последнее число IP', 'Last IP octet'), 'last_octet')
        self.naming_mode.setAccessibleName(_t('Часть worker после точки', 'Worker suffix after dot'))
        naming_layout.addWidget(self.naming_mode, 1)
        self.start_label = QLabel(_t('Начало', 'Start'))
        self.start = QSpinBox()
        self.start.setRange(1, 1_000_000_000)
        self.start.setAccessibleName(_t('Первый номер worker', 'First worker number'))
        self.start_label.setBuddy(self.start)
        naming_layout.addWidget(self.start_label)
        naming_layout.addWidget(self.start)
        self.width_label = QLabel(_t('Разрядов', 'Digits'))
        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 9)
        self.width_spin.setAccessibleName(_t('Минимальное число разрядов номера', 'Minimum number of digits'))
        self.width_label.setBuddy(self.width_spin)
        naming_layout.addWidget(self.width_label)
        naming_layout.addWidget(self.width_spin)
        naming_group_layout.addLayout(naming_layout)
        settings_layout.addWidget(naming_box)
        numbering = QLabel(_t('Нумерация — по возрастанию IP. После предпросмотра номера фиксируются; ошибки не сдвигают номера других ASIC.',
                              'Numbering follows ascending IP addresses. Preview freezes the numbers; errors do not renumber other ASICs.'))
        numbering.setWordWrap(True)
        numbering.setObjectName('Muted')
        settings_layout.addWidget(numbering)
        settings_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.settings_scroll = QScrollArea()
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.settings_scroll.setWidget(self.settings)
        self.settings_scroll.setMinimumHeight(180)
        root.addWidget(self.settings_scroll, 1)
        self.error = QLabel()
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        self.error.setAccessibleName(_t('Ошибка настройки', 'Settings error'))
        root.addWidget(self.error)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setObjectName('SelectionCount')
        root.addWidget(self.summary)
        self.export_status = QLabel()
        self.export_status.setWordWrap(True)
        self.export_status.setTextFormat(Qt.TextFormat.PlainText)
        self.export_status.setAccessibleName(_t('Результат сохранения сведений о совместимости', 'Compatibility evidence save result'))
        root.addWidget(self.export_status)
        self.model = PoolPreviewModel(self.targets, self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setAccessibleName(_t('Изменения и результаты по устройствам', 'Changes and results per device'))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setDefaultSectionSize(max(34, self.table.fontMetrics().height() + 10))
        for column, width in enumerate((118, 145, 150, 48, 75, 180, 180, 145, 120, 280)):
            self.table.setColumnWidth(column, width)
        root.addWidget(self.table, 1)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setTabChangesFocus(True)
        self.details.setAccessibleName(_t('Полные подробности выбранного устройства', 'Full details of the selected device'))
        self.details.setMinimumHeight(66)
        self.details.setMaximumHeight(95)
        self.details.setPlaceholderText(_t('Выберите строку, чтобы прочитать полный результат проверки или записи.',
                                           'Select a row to read the full review or write result.'))
        root.addWidget(self.details)
        self.table.selectionModel().selectionChanged.connect(self._update_details)
        self.model.dataChanged.connect(self._update_details)
        self.model.modelReset.connect(self._update_details)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setAccessibleName(_t('Выполнение настройки пулов', 'Pool settings progress'))
        root.addWidget(self.progress)
        self.note = QLabel()
        self.note.setObjectName('Muted')
        self.note.setWordWrap(True)
        root.addWidget(self.note)
        buttons = QHBoxLayout()
        self.phase_label = QLabel()
        self.phase_label.setWordWrap(True)
        buttons.addWidget(self.phase_label, 1)
        self.preview_button = QPushButton(_t('Прочитать и показать изменения', 'Read and preview changes'))
        self.preview_button.clicked.connect(self.start_preview)
        self.apply_button = QPushButton(_t('Применить', 'Apply'))
        self.apply_button.setProperty('primary', True)
        self.apply_button.clicked.connect(self.start_apply)
        self.cancel_button = QPushButton(_t('Остановить', 'Stop'))
        self.cancel_button.clicked.connect(self.request_stop)
        self.close_button = QPushButton(_t('Закрыть', 'Close'))
        self.close_button.clicked.connect(self.reject)
        for button in (self.preview_button, self.apply_button, self.cancel_button, self.close_button):
            buttons.addWidget(button)
        root.addLayout(buttons)
        for control, signal in ((self.worker_name, 'textChanged'), (self.naming_mode, 'currentIndexChanged'),
                                (self.start, 'valueChanged'), (self.width_spin, 'valueChanged'),
                                (self.keep_worker_name, 'toggled')):
            getattr(control, signal).connect(self._form_changed)
        self._building = False
        self._fit_columns()
        self._refresh()

    @property
    def active(self):
        return self._job is not None

    @staticmethod
    def _is_whatsminer(row):
        identity = row.get('Identity')
        identity = identity if isinstance(identity, dict) else {}
        make = str(row.get('Make') or identity.get('make') or '').casefold()
        profile = str(row.get('ProfileId') or identity.get('profile_id') or '')
        return make in {'microbt', 'whatsminer'} or profile.startswith('microbt.')

    def _form_changed(self, *args):
        if self._building or self.active:
            return
        self.plans.clear()
        self.results.clear()
        self._evidence_by_ip.clear()
        self.export_status.clear()
        self._checked = 0
        self._read_only_evidence = False
        self.phase = 'editing'
        self.model.reset()
        self.error.clear()
        self._refresh()

    def _toggle_settings(self, expanded):
        if hasattr(self, 'settings'):
            self.settings.setVisible(expanded)
        if hasattr(self, 'settings_scroll'):
            self.settings_scroll.setVisible(expanded)
        self.settings_toggle.setText(_t('Скрыть параметры изменения', 'Hide change settings')
            if expanded else _t('Показать параметры изменения', 'Show change settings'))
        if not self._building:
            self._refresh()

    def export_evidence(self):
        """Save only detached, whitelisted review data after explicit selection."""
        if self.active or not self._evidence_by_ip:
            return
        path, _ = QFileDialog.getSaveFileName(self,
            _t('Сохранить сведения о совместимости', 'Save compatibility evidence'),
            'asic-pool-compatibility.json', 'JSON (*.json)')
        if not path:
            return
        output = None
        try:
            # Re-whitelist at the export boundary; private map keys are never sent.
            report = evidence_report(tuple(self._evidence_by_ip.values()))
            count = len(report['devices'])
            encoded = (json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
            output = QSaveFile(path)
            if not output.open(QIODevice.OpenModeFlag.WriteOnly):
                raise OSError
            if output.write(encoded) != len(encoded):
                raise OSError
            if not output.commit():
                raise OSError
            self.export_status.setText(_t(f'Сохранено записей о совместимости: {count}.',
                                          f'Compatibility records saved: {count}.'))
        except (OSError, ValueError, TypeError, KeyError):
            if output is not None and output.isOpen():
                output.cancelWriting()
            self.export_status.setText(_t(
                'Не удалось сохранить сведения о совместимости. Проверьте выбранный путь и доступ к файлу.',
                'Could not save compatibility evidence. Check the selected path and file permissions.'))

    def _update_details(self, *args):
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            self.details.clear()
            return
        row = self.model.rows[selected[0].row()]
        lines = [f'{row[0]} · {row[1]} · {row[2]}']
        if row[3] != '—':
            lines.append(_t('Пул', 'Pool') + f' {row[3]} · {row[4]}: {row[5]} → {row[6]}')
        lines.append(_status(row[7]) if self._read_only_evidence else
            _status(row[7]) + ' · ' + _t('Принято API', 'API accepted') + ': ' + row[8])
        if row[9]:
            lines.append(tr_error(row[9]))
        self.details.setPlainText(redact('\n'.join(lines)))

    def _fit_columns(self):
        if not hasattr(self, 'table'):
            return
        compact = self.width() < 1050
        layout = (compact, self._read_only_evidence)
        if layout == getattr(self, '_column_layout', None):
            return
        self._column_layout = layout
        # Change stretching before hiding sections: Qt otherwise retains gaps
        # from the hidden columns until a later header layout pass.
        self.table.horizontalHeader().setStretchLastSection(not compact)
        hidden = ({3, 4, 5, 6, 8} | ({9} if compact else set())
                  if self._read_only_evidence else {1, 2, 9} if compact else set())
        for column in range(self.model.columnCount()):
            self.table.setColumnHidden(column, column in hidden)
        if self._read_only_evidence:
            for column, width in ((0, 108), (1, 180), (2, 180), (7, 170), (9, 280)):
                self.table.setColumnWidth(column, width)
        elif compact:
            for column, width in ((0, 108), (3, 44), (4, 60), (5, 130), (6, 130), (7, 140), (8, 112)):
                self.table.setColumnWidth(column, width)
        else:
            for column, width in enumerate((118, 145, 150, 48, 75, 180, 180, 145, 120, 280)):
                self.table.setColumnWidth(column, width)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_columns()

    def edits(self):
        naming = None
        if any(editor.worker.isChecked() for editor in self.slots):
            name = None if self.keep_worker_name.isChecked() else self.worker_name.text().strip()
            if name is not None and (not name or '.' in name):
                raise ValueError(_t('Введите общее имя worker до точки, без точки.',
                                    'Enter the common worker name before the dot, without a dot.'))
            naming = WorkerNaming(name=name, mode=self.naming_mode.currentData(),
                                  start=self.start.value(), width=self.width_spin.value())
        edits = []
        for editor in self.slots:
            values = {}
            for field, mode, control in (('url', editor.url_mode, editor.url),
                                          ('password', editor.password_mode, editor.password)):
                if mode.currentIndex() == 1:
                    if not control.text():
                        self.tabs.setCurrentIndex(editor.slot)
                        control.setFocus()
                        raise ValueError(_t('Введите значение выбранного поля или выберите «Очистить».',
                                            'Enter a value for the selected field or choose Clear.'))
                    values[field] = control.text()
                elif mode.currentIndex() == 2:
                    values[field] = ''
            if editor.worker.isChecked():
                values['worker'] = naming
            if values:
                edits.append(PoolEdit(slot=editor.slot, **values))
        if not edits:
            raise ValueError(_t('Выберите хотя бы одно поле для изменения.', 'Select at least one field to change.'))
        return edits

    def start_preview(self):
        if self.active:
            return
        try:
            edits = self.edits()
        except ValueError as exc:
            # UI validation messages are local constants; do not echo input values.
            self.settings_toggle.setChecked(True)
            self.error.setText(tr_error(str(exc)))
            return
        self._reset_review(evidence_only=False)
        self._start_job(_PoolJob(self.service, 'checking', self.targets, edits, parent=self))

    def start_evidence(self):
        if self.active or not self.targets:
            return
        self._reset_review(evidence_only=True)
        self._start_job(_PoolJob(self.service, 'checking', self.targets, evidence_only=True, parent=self))

    def _reset_review(self, *, evidence_only):
        self._read_only_evidence = evidence_only
        self.error.clear()
        self.plans.clear()
        self.results.clear()
        self._evidence_by_ip.clear()
        self.export_status.clear()
        self._checked = 0
        self._attempted.clear()
        self.model.reset('checking')
        self.settings_toggle.setChecked(False)

    def _start_job(self, job):
        self._job = job
        self.phase = job.phase
        self._stop_requested = self._failed = False
        job.plan_ready.connect(self.add_plan)
        job.result_ready.connect(self.set_result)
        job.failed.connect(self._job_failed)
        job.finished.connect(self._job_finished)
        self._refresh()
        job.start()

    def add_plan(self, plan):
        self.plans[plan.ip] = plan
        self._evidence_by_ip[plan.ip] = evidence_from_plan(plan)
        self._checked = len(self.plans)
        self.model.add_plan(plan)
        self._refresh()

    def start_apply(self):
        if self.active or self.phase != 'prepared' or self._read_only_evidence:
            return
        plans = [self.plans[row['IP']] for row in self.targets
                 if row['IP'] in self.plans and self.plans[row['IP']].status == 'ready']
        if not plans:
            return
        self._attempted = {plan.ip for plan in plans}
        self.results.clear()
        for ip in self._attempted:
            self.model.set_status(ip, 'executing')
        self._start_job(_PoolJob(self.service, 'executing', plans=plans, parent=self))

    def set_result(self, ip, result):
        if ip not in self._attempted:
            return
        self.results[ip] = result
        evidence = self._evidence_by_ip.get(ip)
        if evidence is not None:
            evidence['result_status'] = result.status
        self.model.set_result(ip, result)
        self._refresh()

    def _job_failed(self, message=''):
        self._failed = True
        if message:
            self.error.setText(redact(tr_error(message)))
        elif self._read_only_evidence:
            self.error.setText(_t('Не удалось завершить чтение сведений о совместимости. Настройки ASIC не изменялись.',
                                  'Compatibility evidence reading could not finish. ASIC settings were not changed.'))
        else:
            self.error.setText(_t('Не удалось завершить обработку. Результаты уже отправленных команд могут быть неизвестны.',
                                  'Processing could not finish. Commands already sent may have an unknown result.'))

    def _job_finished(self):
        job = self._job
        if job is None:
            return
        was_execution = self.phase == 'executing'
        if was_execution:
            for ip in self._attempted - self.results.keys():
                self.set_result(ip, CommandResult('', 'unconfirmed',
                    _t('Итог не получен. Запись автоматически не повторяется.',
                       'No final result received. The write is not retried automatically.')))
        else:
            for target in self.targets:
                if target['IP'] not in self.plans:
                    self.model.set_status(target['IP'], 'cancelled' if self._stop_requested else 'failed',
                        _t('Проверка не завершена.', 'Review did not finish.'))
        self._job = None
        job.edits = job.plans = ()
        job.deleteLater()
        self.phase = 'finished' if was_execution or self._stop_requested or self._failed else 'prepared'
        if was_execution:
            # Release raw configs/passwords once a plan has been consumed.
            self.plans.clear()
            for editor in self.slots:
                with QSignalBlocker(editor.password):
                    editor.password.clear()
            self.completed.emit()
        elif self._stop_requested or self._failed:
            self.plans.clear()
        self._refresh()

    def request_stop(self):
        if self._job is not None and not self._stop_requested:
            self._stop_requested = True
            self._job.cancel.set()
            self._refresh()

    def _refresh(self):
        self._fit_columns()
        active = self.active
        reviewing = self.phase != 'editing' and not self.settings_toggle.isChecked()
        # Give the editable form enough room in small windows. Empty or folded
        # review panels must not squeeze pool/password inputs out of view.
        self.table.setVisible(reviewing)
        self.details.setVisible(reviewing)
        self.note.setVisible(reviewing)
        ready = 0 if self._read_only_evidence else sum(plan.status == 'ready' for plan in self.plans.values())
        checked = self._checked
        confirmed = sum(result.status == 'succeeded' for result in self.results.values())
        accepted = sum(bool(result.accepted_by_api) for result in self.results.values())
        self.summary.setText(_t(
            f'Выбрано: {len(self.targets)} · Проверено: {checked} · Готово: {ready} · Принято API: {accepted} · Подтверждено: {confirmed}',
            f'Selected: {len(self.targets)} · Checked: {checked} · Ready: {ready} · API accepted: {accepted} · Confirmed: {confirmed}'))
        if self._read_only_evidence:
            observed = len({row[0] for row in self.model.rows if row[7] == 'observed'})
            self.summary.setText(_t(f'Выбрано: {len(self.targets)} · Проверено: {checked} · Сведения прочитаны: {observed} · Только чтение',
                                    f'Selected: {len(self.targets)} · Checked: {checked} · Evidence collected: {observed} · Read only'))
        self.note.setText(_t(
            'В этом режиме читаются только технические сведения. Настройки ASIC не меняются; совместимость записи не проверяется.',
            'This mode only reads technical evidence. ASIC settings are not changed; write compatibility is not tested.')
            if self._read_only_evidence else _t(
                'Пароли скрыты в предпросмотре. «Принято API» и «Подтверждено чтением» — разные результаты. Автоматического повтора записи нет.',
                'Preview hides passwords. API acceptance and readback confirmation are different outcomes. Writes are never retried automatically.'))
        self.settings.setEnabled(not active)
        self.settings_toggle.setEnabled(not active)
        self.worker_name.setEnabled(not self.keep_worker_name.isChecked())
        sequential = self.naming_mode.currentData() == 'index'
        for widget in (self.start, self.width_spin, self.start_label, self.width_label):
            widget.setEnabled(sequential)
        self.preview_button.setEnabled(not active and bool(self.targets))
        self.evidence_button.setEnabled(not active and bool(self.targets))
        self.export_button.setEnabled(not active and bool(self._evidence_by_ip))
        self.apply_button.setEnabled(not active and self.phase == 'prepared' and ready > 0)
        self.apply_button.setText(_t(f'Применить для {ready}', f'Apply to {ready}'))
        self.cancel_button.setVisible(active)
        self.cancel_button.setEnabled(not self._stop_requested)
        self.close_button.setEnabled(not active)
        self.progress.setVisible(active)
        self.progress.setRange(0, len(self.targets) if self.phase == 'checking' else len(self._attempted))
        self.progress.setValue(checked if self.phase == 'checking' else len(self.results))
        self.phase_label.setText(_t('Останавливаем…', 'Stopping…') if active and self._stop_requested else {
            'editing': _t('Выберите поля для изменения.', 'Choose fields to change.'),
            'checking': _t('Читаем настройки; запись не выполняется.', 'Reading settings; no writes.'),
            'prepared': _t('Проверьте значения перед применением.', 'Review values before applying.'),
            'executing': _t('Записываем и проверяем чтением.', 'Writing and verifying by readback.'),
            'finished': _t('Операция завершена. Для новой записи обновите предпросмотр.',
                           'Operation finished. Refresh preview before another write.'),
        }[self.phase])
        if self._read_only_evidence and not (active and self._stop_requested):
            self.phase_label.setText(_t('Читаем сведения; настройки ASIC не меняются.',
                                        'Reading evidence; ASIC settings are not changed.') if active else
                _t('Чтение завершено. Можно сохранить сведения о совместимости.',
                   'Reading finished. Compatibility evidence can be saved.'))

    def reject(self):
        if self.active:
            self.request_stop()
            return
        self.plans.clear()
        self._evidence_by_ip.clear()
        for editor in self.slots:
            with QSignalBlocker(editor.password):
                editor.password.clear()
        super().reject()

    def closeEvent(self, event):
        if self.active:
            self.request_stop()
            event.ignore()
        else:
            self.plans.clear()
            self._evidence_by_ip.clear()
            for editor in self.slots:
                with QSignalBlocker(editor.password):
                    editor.password.clear()
            super().closeEvent(event)
