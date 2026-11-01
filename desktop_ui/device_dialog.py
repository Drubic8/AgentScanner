"""Read-only device card and cancellable, on-demand log collection.

The card keeps the selected scan snapshot immutable. Device text is always shown
as plain text; support exports use a separate, previewable redacted bundle.
"""
from copy import deepcopy
from datetime import datetime
from html import escape
from pathlib import Path
from threading import Event

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFontDatabase, QTextCursor, QTextDocument
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDialog,
    QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from miner_scanner.access import access_family
from miner_scanner.device_logs import (DeviceLogResult, load_log_file,
    read_device_log, supported_log_kinds)
from miner_scanner.diagnostic_bundle import build_bundle, bundle_preview, write_bundle
from .i18n import action_label, get_language


TEXT = {
    'title': ('Карточка устройства', 'Device details'),
    'overview': ('Обзор', 'Overview'), 'pools': ('Пулы', 'Pools'),
    'logs': ('Логи устройства', 'Device logs'), 'diagnostics': ('Диагностика', 'Diagnostics'),
    'snapshot': ('Снимок из таблицы на момент открытия. Для новых показаний повторите сканирование и откройте карточку заново.',
                 'Scan snapshot captured when this card was opened. Scan again and reopen the card for new readings.'),
    'stale': ('Устаревшие данные — текущее состояние не подтверждено.',
              'Stale data — the current device state is not confirmed.'),
    'identity': ('Устройство и прошивка', 'Device and firmware'),
    'telemetry': ('Показания сканирования', 'Scan readings'),
    'commands': ('Доступные команды по снимку', 'Commands supported in this snapshot'),
    'none_commands': ('Подтверждённых команд нет.', 'No confirmed commands.'),
    'commands_hint': ('Выполнение команд доступно в основной таблице после проверки совместимости.',
                      'Run commands from the main table after compatibility checks.'),
    'model': ('Модель', 'Model'), 'make': ('Производитель', 'Manufacturer'),
    'firmware': ('Прошивка', 'Firmware'), 'version': ('Версия прошивки', 'Firmware version'),
    'profile': ('Профиль API', 'API profile'), 'state': ('Состояние', 'State'),
    'observed': ('Время показаний', 'Observed at'), 'uptime': ('Время работы', 'Uptime'),
    'temperature': ('Температуры', 'Temperatures'), 'fans': ('Вентиляторы', 'Fans'),
    'errors': ('Ошибки', 'Errors'), 'missing': ('Нет данных', 'Unavailable'),
    'pool_hint': ('Настроенные слоты 1–3. Их порядок не обозначает активный пул. Пустые значения означают, что сканер не получил данные слота.',
                  'Configured slots 1–3. Their order does not indicate the active pool. Empty values mean the scanner did not receive that slot.'),
    'pool_stale': ('Данные пулов устарели и скрыты до успешного сканирования.',
                   'Pool data is stale and hidden until a successful scan.'),
    'log_hint': ('Чтение запускается вручную. Лог показан как получен и может содержать личные данные. Для передачи используйте очищенный экспорт.',
                 'Logs are fetched on demand. The original text may contain private information. Use the redacted export for sharing.'),
    'whatsminer_hint': ('WhatsMiner сначала готовит архив: чтение может занять до 60 секунд. Из больших логов показывается конец — до 2 МиБ.',
                        'WhatsMiner prepares an archive first: reading may take up to 60 seconds. For large logs, the latest portion is shown, up to 2 MiB.'),
    'whatsminer_loading': ('WhatsMiner готовит и передаёт архив… Ожидание до 60 секунд.',
                           'WhatsMiner is preparing and transferring the archive… Allow up to 60 seconds.'),
    'whatsminer_too_large': ('Архив WhatsMiner превышает лимит размера или числа файлов. Можно открыть отдельный текстовый лог до 2 МиБ.',
                             'The WhatsMiner archive exceeds the size or file-count limit. You can open a separate text log up to 2 MiB.'),
    'whatsminer_truncated': ('Показан конец лога (до 2 МиБ).', 'The latest portion of the log is shown (up to 2 MiB).'),
    'miner': ('Майнер', 'Miner'), 'system': ('Система', 'System'),
    'kind': ('Тип лога', 'Log type'), 'fetch': ('Получить лог', 'Fetch log'),
    'cancel': ('Отменить', 'Cancel'), 'open': ('Открыть файл…', 'Open file…'),
    'search': ('Поиск в логе…', 'Search this log…'),
    'previous': ('Назад', 'Previous'), 'next': ('Далее', 'Next'),
    'copy': ('Копировать', 'Copy'), 'save_log': ('Сохранить очищенный лог…', 'Save redacted log…'),
    'no_log': ('Получите лог устройства или откройте сохранённый файл размером до 2 МиБ.',
               'Fetch a device log or open a saved text file up to 2 MiB.'),
    'unsupported': ('Автоматическое чтение логов этого профиля пока не поддерживается. Можно открыть сохранённый файл.',
                    'Automatic log reading is not supported for this profile yet. You can open a saved file.'),
    'loading': ('Чтение лога…', 'Reading log…'), 'cancelling': ('Отмена чтения…', 'Cancelling…'),
    'succeeded': ('Лог получен', 'Log loaded'),
    'file_loaded': ('Лог загружен из файла', 'Log loaded from file'),
    'empty_log': ('Лог получен и пока пуст.', 'The log was loaded and is currently empty.'),
    'auth_required': ('Не удалось войти. Проверьте профили в настройках «Доступ к ASIC».',
                      'Authentication failed. Check the ASIC access profiles in settings.'),
    'unavailable': ('Устройство или выбранный лог недоступны.', 'The device or selected log is unavailable.'),
    'invalid_response': ('Получен неподдерживаемый формат. Лог не загружен.',
                         'Unsupported response format. No log was loaded.'),
    'cancelled': ('Чтение отменено.', 'Reading cancelled.'),
    'too_large': ('Лог превышает ограничение 2 МиБ. Откройте меньший текстовый фрагмент.',
                  'The log exceeds 2 MiB. Open a smaller text excerpt.'),
    'timeout': ('Время ожидания истекло.', 'The request timed out.'),
    'error': ('Не удалось получить лог.', 'Could not load the log.'),
    'imported': ('Файл', 'File'), 'source': ('Источник', 'Source'), 'received': ('Получен', 'Collected'),
    'truncated': ('Показан ограниченный фрагмент.', 'A limited excerpt is shown.'),
    'not_found': ('Совпадений нет.', 'No matches.'), 'found': ('Совпадение выделено.', 'Match selected.'),
    'copied': ('Текст скопирован.', 'Text copied.'),
    'preview_hint': ('Предпросмотр очищенного пакета: сведения об ASIC, время снимка и лог. Проверьте текст перед передачей — автоматическое скрытие не распознаёт все личные данные. Данные никуда не отправляются.',
                     'Preview the redacted package: ASIC details, snapshot time and log. Review it before sharing — automatic redaction cannot identify every private value. Nothing is uploaded.'),
    'save_bundle': ('Сохранить пакет ZIP…', 'Save ZIP package…'),
    'preview_failed': ('Не удалось подготовить очищенный пакет. Экспорт недоступен.',
                       'Could not prepare the redacted package. Export is unavailable.'),
    'saved': ('Файл сохранён.', 'File saved.'),
    'save_failed': ('Не удалось сохранить файл. Проверьте папку и выберите другое имя.',
                    'Could not save the file. Check the folder and choose another name.'),
    'exists': ('Файл уже существует. Выберите другое имя.', 'The file already exists. Choose a different name.'),
    'close': ('Закрыть', 'Close'),
    'running': ('Работает', 'Running'), 'sleeping': ('Сон', 'Sleeping'),
    'idle': ('Ожидание', 'Idle'), 'offline': ('Оффлайн', 'Offline'),
    'stopping': ('Останавливается', 'Stopping'),
    'unknown': ('Неизвестно', 'Unknown'), 'state_error': ('Ошибка', 'Error'),
    'disabled': ('Не поддерживается', 'Not supported'),
}


def _plain_label(text='', *, muted=False):
    label = QLabel(str(text))
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if muted:
        label.setObjectName('Muted')
    return label


def _value(value):
    if value is None or value == '':
        return '—'
    if isinstance(value, (str, int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return ' · '.join(str(item) for item in value if isinstance(item, (str, int, float))) or '—'
    return '—'


class _LogWorker(QThread):
    """No QObject widgets or callbacks into the GUI from this thread."""
    def __init__(self, row, profiles, kind, path, version, secrets, parent):
        super().__init__(parent)
        self.row, self.profiles = row, profiles
        self.kind, self.path = kind, path
        self.version, self.secrets = version, secrets
        self.cancel = Event()
        self.result = None
        self.bundle = None

    def run(self):
        try:
            if self.cancel.is_set():
                result = DeviceLogResult('cancelled', kind=self.kind)
            elif self.path is not None:
                result = load_log_file(self.path)
            else:
                result = read_device_log(self.row, self.kind,
                    access_profiles=self.profiles, cancel=self.cancel)
            if self.cancel.is_set():
                result = DeviceLogResult('cancelled', kind=self.kind)
            self.result = result
            self.bundle = build_bundle(self.row, result, app_version=self.version,
                                       secrets=self.secrets)
        except Exception:
            # Exceptions can contain passwords, paths or device-supplied text.
            self.result = DeviceLogResult('cancelled' if self.cancel.is_set() else 'error', kind=self.kind)


class DeviceDialog(QDialog):
    idle = pyqtSignal()

    def __init__(self, row, *, access_profiles, app_version, parent=None):
        super().__init__(parent)
        self.row = deepcopy(row)
        self.access_profiles, self.app_version = access_profiles, app_version
        self.language = get_language()
        self._worker = None
        self._close_pending = False
        self.log_result = None
        self.bundle = None
        self._secrets = self._access_secrets()
        self.setWindowTitle(f"{self._t('title')} · {_value(self.row.get('IP'))}")
        self.resize(1000, 740)
        self.setMinimumSize(740, 540)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(10)
        self.heading = _plain_label(_value(self.row.get('Model')))
        self.heading.setObjectName('DialogTitle')
        root.addWidget(self.heading)
        self.identity_line = _plain_label(' · '.join(_value(self.row.get(key))
            for key in ('IP', 'Firmware', 'FirmwareVersion') if self.row.get(key)), muted=True)
        root.addWidget(self.identity_line)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self._overview()
        self._pools()
        self._logs()
        self._diagnostics()
        footer = QHBoxLayout()
        self.notice = _plain_label('', muted=True)
        footer.addWidget(self.notice, 1)
        self.close_button = QPushButton(self._t('close'))
        self.close_button.clicked.connect(self.reject)
        footer.addWidget(self.close_button)
        root.addLayout(footer)
        self._prepare_initial_bundle()

    def _t(self, key):
        return TEXT[key][self.language == 'en']

    @property
    def active(self):
        return self._worker is not None

    @property
    def stale(self):
        telemetry = self.row.get('Telemetry')
        return bool(self.row.get('Stale') or isinstance(telemetry, dict) and telemetry.get('stale'))

    def _access_secrets(self):
        try:
            candidates = self.access_profiles.candidates(self.row.get('IP', ''),
                access_family(self.row.get('ProfileId')))
            return tuple(value for candidate in candidates
                         for value in (candidate.username, candidate.password) if value)
        except (AttributeError, ValueError, TypeError):
            return ()

    def _overview(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(12, 16, 12, 12)
        layout.setSpacing(12)
        layout.addWidget(_plain_label(self._t('snapshot'), muted=True))
        self.stale_label = _plain_label(self._t('stale'))
        self.stale_label.setObjectName('ValidationError')
        self.stale_label.setVisible(self.stale)
        layout.addWidget(self.stale_label)
        identity = self.row.get('Identity')
        identity = identity if isinstance(identity, dict) else {}
        telemetry = self.row.get('Telemetry')
        telemetry = telemetry if isinstance(telemetry, dict) else {}
        self.overview_values = {}
        groups = QGridLayout()
        identity_box = QGroupBox(self._t('identity'))
        identity_form = QFormLayout(identity_box)
        identity_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        for key, title, value in (
            ('IP', 'IP', self.row.get('IP')),
            ('Model', self._t('model'), self.row.get('Model') or identity.get('model')),
            ('Make', self._t('make'), self.row.get('Make') or identity.get('make')),
            ('Firmware', self._t('firmware'), self.row.get('Firmware') or identity.get('firmware')),
            ('FirmwareVersion', self._t('version'), self.row.get('FirmwareVersion') or identity.get('firmware_version')),
            ('ProfileId', self._t('profile'), self.row.get('ProfileId') or identity.get('profile_id')),
            ('Serial', 'SN', identity.get('serial') or self.row.get('Serial')),
            ('MAC', 'MAC', identity.get('mac') or self.row.get('MAC'))):
            label = _plain_label(_value(value))
            self.overview_values[key] = label
            identity_form.addRow(_plain_label(title, muted=True), label)
        groups.addWidget(identity_box, 0, 0)
        reading_box = QGroupBox(self._t('telemetry'))
        reading_form = QFormLayout(reading_box)
        reading_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        raw_state = self.row.get('Status') or telemetry.get('mining_state') or 'unknown'
        state_key = {'running': 'running', 'mining': 'running', 'sleep': 'sleeping',
            'sleeping': 'sleeping', 'idle': 'idle', 'waitwork': 'idle',
            'stopping': 'stopping', 'offline': 'offline',
            'error': 'state_error', 'unknown': 'unknown'}.get(str(raw_state).lower())
        state = self._t('unknown') if self.stale else self._t(state_key) if state_key else _value(raw_state)
        readings = (
            ('State', self._t('state'), state),
            ('ObservedAt', self._t('observed'), telemetry.get('observed_at') or self.row.get('ObservedAt')),
            ('Real', 'Real Hash', self.row.get('Real')),
            ('Avg', 'Avg Hash', self.row.get('Avg')),
            ('Uptime', self._t('uptime'), self.row.get('Uptime')),
            ('Temp', self._t('temperature'), self.row.get('Temp')),
            ('Fan', self._t('fans'), self.row.get('Fan')),
            ('Error', self._t('errors'), self.row.get('Error')))
        for key, title, value in readings:
            if self.stale and key in ('Real', 'Avg', 'Uptime', 'Temp', 'Fan'):
                value = None
            label = _plain_label(_value(value))
            self.overview_values[key] = label
            reading_form.addRow(_plain_label(title, muted=True), label)
        groups.addWidget(reading_box, 0, 1)
        groups.setColumnStretch(0, 1)
        groups.setColumnStretch(1, 1)
        layout.addLayout(groups)
        capability_box = QGroupBox(self._t('commands'))
        capabilities = QVBoxLayout(capability_box)
        values = self.row.get('Capabilities')
        values = values if isinstance(values, dict) else {}
        available = [action_label(code) for code, status in values.items() if status == 'supported']
        self.capabilities_label = _plain_label(' · '.join(available) if available else self._t('none_commands'))
        capabilities.addWidget(self.capabilities_label)
        capabilities.addWidget(_plain_label(self._t('commands_hint'), muted=True))
        layout.addWidget(capability_box)
        layout.addStretch()
        scroll.setWidget(body)
        self.tabs.addTab(scroll, self._t('overview'))

    def _pools(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 16, 12, 12)
        layout.addWidget(_plain_label(self._t('pool_stale' if self.stale else 'pool_hint'), muted=True))
        self.pool_table = QTableWidget(3, 3)
        self.pool_table.setHorizontalHeaderLabels(['Slot', 'Pool', 'Worker'])
        self.pool_table.verticalHeader().hide()
        self.pool_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pool_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.pool_table.setAlternatingRowColors(True)
        self.pool_table.setWordWrap(True)
        self.pool_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.pool_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.pool_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for index in range(3):
            suffix = '' if index == 0 else str(index + 1)
            values = [str(index + 1), *('—' if self.stale else _value(self.row.get(key + suffix))
                                       for key in ('Pool', 'Worker'))]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip('<pre>' + escape(value) + '</pre>')
                self.pool_table.setItem(index, column, item)
            self.pool_table.setRowHeight(index, 72)
        layout.addWidget(self.pool_table)
        self.tabs.addTab(page, self._t('pools'))

    def _logs(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 16, 12, 12)
        kinds = supported_log_kinds(self.row)
        identity = self.row.get('Identity')
        identity = identity if isinstance(identity, dict) else {}
        self._whatsminer_logs = bool(kinds) and (
            self.row.get('ProfileId') or identity.get('profile_id')) == 'microbt.rpc3'
        hint = self._t('log_hint')
        if self._whatsminer_logs:
            hint += '\n' + self._t('whatsminer_hint')
        self.log_hint = _plain_label(hint, muted=True)
        layout.addWidget(self.log_hint)
        controls = QHBoxLayout()
        self.kind_box = QComboBox()
        self.kind_box.setAccessibleName(self._t('kind'))
        for kind in kinds:
            self.kind_box.addItem(self._t(kind), kind)
        if not kinds:
            self.kind_box.addItem(self._t('disabled'), '')
        self.kind_box.setEnabled(bool(kinds))
        controls.addWidget(self.kind_box)
        self.fetch_button = QPushButton(self._t('fetch'))
        self.fetch_button.setProperty('primary', True)
        self.fetch_button.setEnabled(bool(kinds))
        self.fetch_button.clicked.connect(self.fetch_log)
        controls.addWidget(self.fetch_button)
        self.cancel_button = QPushButton(self._t('cancel'))
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.request_stop)
        controls.addWidget(self.cancel_button)
        controls.addStretch()
        self.open_button = QPushButton(self._t('open'))
        self.open_button.clicked.connect(self.open_file)
        controls.addWidget(self.open_button)
        layout.addLayout(controls)
        self.log_status = _plain_label(self._t('no_log' if kinds else 'unsupported'))
        layout.addWidget(self.log_status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(4)
        self.progress.setTextVisible(False)
        self.progress.hide()
        layout.addWidget(self.progress)
        search = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(self._t('search'))
        self.search.setAccessibleName(self._t('search'))
        self.search.returnPressed.connect(self.find_next)
        search.addWidget(self.search, 1)
        self.previous_button = QPushButton(self._t('previous'))
        self.previous_button.clicked.connect(lambda: self.find_next(backward=True))
        self.next_button = QPushButton(self._t('next'))
        self.next_button.clicked.connect(self.find_next)
        search.addWidget(self.previous_button)
        search.addWidget(self.next_button)
        layout.addLayout(search)
        self.log_text = self._text_edit(self._t('logs'))
        self.log_text.setPlaceholderText(self._t('no_log'))
        layout.addWidget(self.log_text, 1)
        actions = QHBoxLayout()
        self.copy_button = QPushButton(self._t('copy'))
        self.copy_button.clicked.connect(self.copy_log)
        self.save_log_button = QPushButton(self._t('save_log'))
        self.save_log_button.clicked.connect(self.save_log)
        actions.addWidget(self.copy_button)
        actions.addStretch()
        actions.addWidget(self.save_log_button)
        layout.addLayout(actions)
        self._update_log_actions()
        self.tabs.addTab(page, self._t('logs'))

    def _text_edit(self, accessible_name):
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        editor.setAccessibleName(accessible_name)
        editor.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        return editor

    def _diagnostics(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 16, 12, 12)
        layout.addWidget(_plain_label(self._t('preview_hint'), muted=True))
        self.preview = self._text_edit(self._t('diagnostics'))
        layout.addWidget(self.preview, 1)
        controls = QHBoxLayout()
        controls.addStretch()
        self.export_button = QPushButton(self._t('save_bundle'))
        self.export_button.setProperty('primary', True)
        self.export_button.clicked.connect(self.save_bundle)
        controls.addWidget(self.export_button)
        layout.addLayout(controls)
        self.tabs.addTab(page, self._t('diagnostics'))

    def _prepare_initial_bundle(self):
        try:
            self.bundle = build_bundle(self.row, app_version=self.app_version, secrets=self._secrets)
        except Exception:
            self.bundle = None
        self._update_preview()

    def _update_preview(self):
        try:
            text = bundle_preview(self.bundle) if self.bundle is not None else self._t('preview_failed')
        except Exception:
            text = self._t('preview_failed')
            self.bundle = None
        self.preview.setPlainText(text)
        self.export_button.setEnabled(self.bundle is not None and not self.active)
        self._update_log_actions()

    def _update_log_actions(self):
        available = self.log_result is not None and self.log_result.status == 'succeeded'
        self.copy_button.setEnabled(available and bool(self.log_result.text))
        self.save_log_button.setEnabled(available and self.bundle is not None and not self.active)
        self.previous_button.setEnabled(available)
        self.next_button.setEnabled(available)

    def _start(self, *, path=None):
        if self.active or self._close_pending:
            return
        kind = 'imported' if path is not None else self.kind_box.currentData()
        if not kind:
            return
        self.notice.clear()
        self.log_result = None
        self.bundle = None
        self.log_text.clear()
        self.log_text.setPlaceholderText(self._t('no_log'))
        self.preview.clear()
        self.log_status.setText(self._t('whatsminer_loading'
            if path is None and self._whatsminer_logs else 'loading'))
        self._worker = _LogWorker(self.row, self.access_profiles, kind, path,
                                  self.app_version, self._secrets, self)
        self._worker.finished.connect(self._finish_worker)
        self.fetch_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.kind_box.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.export_button.setEnabled(False)
        self.progress.show()
        self._update_log_actions()
        self._worker.start()

    def fetch_log(self):
        self._start()

    def open_file(self):
        if self.active:
            return
        path, _ = QFileDialog.getOpenFileName(self, self._t('open'), '',
                                             'Text logs (*.txt *.log);;All files (*)')
        if path:
            self._start(path=path)

    def _finish_worker(self):
        worker = self._worker
        if worker is None:
            return
        if worker.cancel.is_set():
            # Cancellation can arrive while redaction runs or after finished was
            # queued. Never present the late successful payload as a fresh log.
            self.log_result = DeviceLogResult('cancelled', kind=worker.kind)
            try:
                self.bundle = build_bundle(self.row, self.log_result,
                    app_version=self.app_version, secrets=self._secrets)
            except Exception:
                self.bundle = None
        else:
            self.log_result = worker.result or DeviceLogResult('error')
            self.bundle = worker.bundle
        self._worker = None
        worker.deleteLater()
        result = self.log_result
        # A failed new fetch never reuses the preceding log or its timestamp.
        self.log_text.setPlainText(result.text if result.status == 'succeeded' else '')
        status = self._t(result.status if result.status in TEXT else 'error')
        whatsminer_result = result.kind != 'imported' and result.source.startswith('get.log.download/')
        if result.status == 'too_large' and whatsminer_result:
            status = self._t('whatsminer_too_large')
        if result.status == 'succeeded':
            if result.kind == 'imported':
                status = self._t('file_loaded')
            if not result.text:
                status = self._t('empty_log')
                self.log_text.setPlaceholderText(status)
            if result.kind in ('miner', 'system', 'imported'):
                status += ' · ' + self._t(result.kind)
            status += f" · {self._t('source')}: {result.source or '—'} · {self._t('received')}: {result.collected_at}"
            if result.truncated:
                status += ' · ' + self._t('whatsminer_truncated' if whatsminer_result else 'truncated')
        self.log_status.setText(status)
        self.progress.hide()
        self.cancel_button.setEnabled(False)
        self.open_button.setEnabled(True)
        kinds = bool(supported_log_kinds(self.row))
        self.kind_box.setEnabled(kinds)
        self.fetch_button.setEnabled(kinds)
        self._update_preview()
        self.idle.emit()
        if self._close_pending:
            self.reject()

    def request_stop(self):
        if self._worker is not None:
            self._worker.cancel.set()
            self.cancel_button.setEnabled(False)
            self.log_status.setText(self._t('cancelling'))

    def find_next(self, checked=False, *, backward=False):
        query = self.search.text()
        if not query:
            return
        flags = QTextDocument.FindFlag.FindBackward if backward else QTextDocument.FindFlag(0)
        found = self.log_text.find(query, flags)
        if not found:
            cursor = self.log_text.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End if backward else QTextCursor.MoveOperation.Start)
            self.log_text.setTextCursor(cursor)
            found = self.log_text.find(query, flags)
        self.notice.setText(self._t('found' if found else 'not_found'))

    def copy_log(self):
        if self.log_result is None or self.log_result.status != 'succeeded':
            return
        cursor = self.log_text.textCursor()
        text = cursor.selectedText().replace('\u2029', '\n') if cursor.hasSelection() else self.log_text.toPlainText()
        QApplication.clipboard().setText(text)
        self.notice.setText(self._t('copied'))

    def _destination(self, title, suffix, filter_text):
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        path, _ = QFileDialog.getSaveFileName(self, title, f'asic-diagnostics-{stamp}{suffix}', filter_text)
        if path and not path.lower().endswith(suffix):
            path += suffix
        return path

    def save_log(self):
        if self.active or self.bundle is None or self.log_result is None or self.log_result.status != 'succeeded':
            return
        path = self._destination(self._t('save_log'), '.txt', 'Text (*.txt)')
        if not path:
            return
        try:
            text = self.bundle['log']['text']
            with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
                stream.write(text)
        except FileExistsError:
            self.notice.setText(self._t('exists'))
        except (OSError, TypeError, KeyError):
            self.notice.setText(self._t('save_failed'))
        else:
            self.notice.setText(self._t('saved'))

    def save_bundle(self):
        if self.active or self.bundle is None:
            return
        path = self._destination(self._t('save_bundle'), '.zip', 'ZIP (*.zip)')
        if not path:
            return
        try:
            write_bundle(path, self.bundle)
        except FileExistsError:
            self.notice.setText(self._t('exists'))
        except (OSError, ValueError, TypeError):
            self.notice.setText(self._t('save_failed'))
        else:
            self.notice.setText(self._t('saved'))

    def reject(self):
        if self.active:
            self._close_pending = True
            self.request_stop()
            return
        super().reject()

    def closeEvent(self, event):
        if self.active:
            self._close_pending = True
            self.request_stop()
            event.ignore()
            return
        super().closeEvent(event)
