"""Explicit, previewed AI diagnostics for one immutable device/log snapshot."""
from datetime import datetime
from pathlib import Path
from threading import Event

from PyQt6.QtCore import Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QFontDatabase
from PyQt6.QtWidgets import (QApplication, QCheckBox, QFileDialog, QHBoxLayout,
    QLabel, QPlainTextEdit, QProgressBar, QPushButton, QTabWidget, QVBoxLayout, QWidget)

from miner_scanner.ai import AnalysisConfig, AnalysisError, analyze, prepare_analysis
from .ai_credentials import AiCredentialStore
from .chatgpt_store import ChatGPTStore
from .preferences import data_directory


TEXT = {
    'title': ('ИИ-анализ', 'AI analysis'),
    'hint': ('Яндекс AI Studio получит только показанные ниже инструкции и очищенные данные. Проверьте текст: автоматическое скрытие может пропустить личную информацию.',
             'Yandex AI Studio receives only the instructions and redacted data shown below. Review the text: automatic redaction can miss private information.'),
    'empty': ('Сначала получите лог на вкладке «Логи устройства» или откройте сохранённый файл.',
              'First fetch a log in Device logs or open a saved text file.'),
    'setup': ('Настройте каталог, модель и ключ в «Настройки → ИИ и диагностика».',
              'Set up the folder, model and key in Settings → AI and diagnostics.'),
    'preview': ('Данные для отправки', 'Request preview'),
    'result': ('Заключение', 'Assessment'),
    'consent': ('Я проверил данные и разрешаю отправить этот запрос в Яндекс AI Studio.',
                'I reviewed the data and allow this request to be sent to Yandex AI Studio.'),
    'analyze': ('Проанализировать', 'Analyze'), 'cancel': ('Отменить', 'Cancel'),
    'copy': ('Копировать заключение', 'Copy assessment'), 'save': ('Сохранить…', 'Save…'),
    'running': ('Выполняется анализ… Сканирование и майнинг продолжают работать.',
                'Analyzing… Scanning and mining continue to operate.'),
    'cancelling': ('Отмена… Уже принятый провайдером запрос может быть оплачен.',
                   'Cancelling… A request already accepted by the provider may still be charged.'),
    'cancelled': ('Анализ отменён. Ответ не использован.', 'Analysis cancelled. The response was discarded.'),
    'ready': ('Запрос подготовлен. Строк включено: {included}; пропущено: {omitted}. Модель: {model}.',
              'Request prepared. Included lines: {included}; omitted: {omitted}. Model: {model}.'),
    'done': ('Заключение относится к этому снимку и логу. Проверьте приведённые свидетельства.',
             'The assessment applies to this snapshot and log. Review the cited evidence.'),
    'credentials': ('Не удалось прочитать защищённый ключ. Введите его заново в настройках.',
                    'Could not read the protected key. Enter it again in settings.'),
    'preparation': ('Не удалось подготовить очищенные данные для анализа.',
                    'Could not prepare redacted analysis data.'),
    'saved': ('Заключение сохранено.', 'Assessment saved.'),
    'save_error': ('Не удалось сохранить файл. Выберите другое имя или папку.',
                   'Could not save the file. Choose another name or folder.'),
    'copied': ('Заключение скопировано.', 'Assessment copied.'),
    'connection': ('Yandex AI Studio · Каталог: {folder} · Модель: {model} · Ключ: {key}',
                   'Yandex AI Studio · Folder: {folder} · Model: {model} · Key: {key}'),
    'configured': ('сохранён', 'saved'), 'not_configured': ('не задан', 'not set'),
    'chatgpt_hint': ('OpenAI получит только показанные ниже инструкции и очищенные данные. Запрос использует ваш план ChatGPT. Проверьте текст перед отправкой.',
                     'OpenAI receives only the instructions and redacted data shown below. This request uses your ChatGPT plan. Review the text before sending.'),
    'chatgpt_consent': ('Я проверил данные и разрешаю этот запрос через мой план ChatGPT.',
                        'I reviewed the data and allow this request using my ChatGPT plan.'),
    'chatgpt_setup': ('В «Настройки → ИИ и диагностика» войдите в ChatGPT, разрешите использование плана и выберите модель аккаунта.',
                      'In Settings → AI and diagnostics, sign in to ChatGPT, allow plan usage and select an account model.'),
    'chatgpt_connection': ('План ChatGPT · Аккаунт: {account} · Модель: {model} · Доступ: {permission}',
                           'Using ChatGPT plan · Account: {account} · Model: {model} · Access: {permission}'),
    'enabled': ('разрешён', 'enabled'), 'disabled': ('не разрешён', 'not enabled'),
    'manage_usage': ('Управление использованием', 'Manage usage'),
    'chatgpt_credentials': ('Сессию ChatGPT не удалось прочитать. Войдите заново в настройках.',
                            'Could not read the ChatGPT session. Sign in again in settings.'),
    'chatgpt_cancelling': ('Отмена… Уже принятый запрос может учитываться в лимите плана ChatGPT.',
                           'Cancelling… An accepted request may still count against your ChatGPT plan allowance.'),
}

ERRORS = {
    'configuration': ('Проверьте каталог и модель в настройках ИИ.', 'Check the folder and model in AI settings.'),
    'invalid_request': ('Яндекс отклонил параметры запроса. Проверьте выбранную модель и ограничения.',
                        'Yandex rejected the request parameters. Check the model and limits.'),
    'model_unavailable': ('Модель недоступна. Выберите доступную модель в настройках.',
                          'The model is unavailable. Select an available model in settings.'),
    'server': ('Сервис Яндекса временно недоступен. Повторите запрос вручную позже.',
               'Yandex is temporarily unavailable. Retry manually later.'),
    'too_large': ('Ответ превышает допустимый размер. Заключение не принято.',
                  'The response exceeds the size limit. No assessment was accepted.'),
    'auth_required': ('Ключ не принят. Проверьте API-ключ в настройках.', 'The API key was rejected. Check settings.'),
    'forbidden': ('Нет доступа к каталогу или модели. Проверьте права сервисного аккаунта.',
                  'Access to the folder or model was denied. Check service account permissions.'),
    'quota': ('Достигнута квота или ограничение запросов. Проверьте биллинг и лимиты Яндекса.',
              'Quota or request limit reached. Check Yandex billing and limits.'),
    'timeout': ('Время ожидания истекло. Автоматического повторного запроса нет.',
                'The request timed out. No automatic retry was sent.'),
    'network': ('Не удалось связаться с Яндекс AI Studio.', 'Could not connect to Yandex AI Studio.'),
    'invalid_response': ('Ответ модели не соответствует формату диагностики. Заключение не принято.',
                         'The response does not match the diagnostic format. No assessment was accepted.'),
    'cancelled': TEXT['cancelled'],
    'error': ('Не удалось выполнить анализ. Проверьте настройки и повторите вручную.',
              'Analysis failed. Check settings and retry manually.'),
}


CHATGPT_ERRORS = {
    'auth_dependency_missing': ('Не установлены компоненты безопасного входа ChatGPT. Обновите зависимости программы и перезапустите её.',
                                'Secure ChatGPT sign-in components are not installed. Update the application dependencies and restart it.'),
    'configuration': ('Войдите в ChatGPT и выберите модель аккаунта в настройках.',
                      'Sign in to ChatGPT and select an account model in settings.'),
    'auth_required': ('Сессия ChatGPT завершилась. Нажмите Continue with ChatGPT в настройках.',
                      'Your ChatGPT session ended. Continue with ChatGPT in settings.'),
    'auth_denied': ('Вход или разрешение доступа отменены. Подключение не изменено.',
                    'Sign-in or consent was declined. The connection was not changed.'),
    'auth_failed': ('Не удалось проверить вход в ChatGPT. Повторите вход в настройках.',
                    'ChatGPT sign-in could not be verified. Sign in again in settings.'),
    'browser_failed': ('Не удалось открыть страницу входа. Проверьте браузер и повторите вручную.',
                       'Could not open the sign-in page. Check your browser and retry manually.'),
    'permission_required': ('Разрешите использование плана через Continue with ChatGPT в настройках.',
                            'Allow plan usage with Continue with ChatGPT in settings.'),
    'chatgpt_permission': ('Разрешите использование плана через Continue with ChatGPT в настройках.',
                           'Allow plan usage with Continue with ChatGPT in settings.'),
    'chatgpt_scope': ('Для этой сессии не разрешены запросы плана ChatGPT. Войдите заново и предоставьте разрешение.',
                      'This session does not permit ChatGPT plan requests. Sign in again and grant permission.'),
    'chatgpt_not_eligible': ('Аккаунт или рабочее пространство не допускает использование плана в приложении.',
                             'This account or workspace is not eligible to use its plan in the app.'),
    'chatgpt_usage_limit': ('Достигнут лимит использования ChatGPT. Проверьте план и лимит приложения через «Управление использованием».',
                            'ChatGPT usage limit reached. Review your plan and app limits in Manage usage.'),
    'chatgpt_usage_unavailable': ('Использование плана ChatGPT временно недоступно. Проверьте настройки ChatGPT.',
                                  'ChatGPT plan usage is currently unavailable. Review ChatGPT settings.'),
    'chatgpt_route': ('Запрос плана ChatGPT недоступен для этого подключения. Проверьте разрешение и регион аккаунта.',
                      'ChatGPT plan requests are unavailable for this connection. Check account permission and region.'),
    'forbidden': ('OpenAI не разрешил запрос для этого аккаунта или региона. Проверьте настройки ChatGPT.',
                  'OpenAI denied this request for the account or region. Review ChatGPT settings.'),
    'quota': ('Достигнут лимит использования ChatGPT. Откройте «Управление использованием».',
              'ChatGPT usage limit reached. Open Manage usage.'),
    'network': ('Не удалось связаться с OpenAI. Автоматического повторного запроса нет.',
                'Could not connect to OpenAI. No automatic retry was sent.'),
    'invalid_request': ('OpenAI отклонил параметры запроса плана ChatGPT.',
                        'OpenAI rejected the ChatGPT plan request parameters.'),
    'model_unavailable': ('Модель недоступна этому аккаунту. Загрузите список моделей в настройках.',
                          'The model is unavailable for this account. Load account models in settings.'),
    'server': ('OpenAI временно недоступен. Повторите вручную позже.',
               'OpenAI is temporarily unavailable. Retry manually later.'),
}


VALIDATION_REASONS = {
    'stream_format': ('Поток ответа имеет некорректную структуру.',
                      'The response stream has an invalid structure.'),
    'stream_json': ('В потоке ответа получены некорректные JSON-данные.',
                    'The response stream contains invalid JSON data.'),
    'stream_incomplete': ('Ответ модели не завершён.', 'The model response is incomplete.'),
    'output_refusal': ('Модель отказалась выполнять этот запрос.',
                       'The model declined this request.'),
    'output_channel': ('Получен ответ из неподдерживаемого канала модели.',
                       'The response came from an unsupported model channel.'),
    'output_missing': ('В ответе модели нет текста заключения.',
                       'The model response contains no assessment text.'),
    'output_tool': ('Модель запросила инструмент вместо заключения.',
                    'The model requested a tool instead of returning an assessment.'),
    'output_json': ('Текст заключения не является корректным JSON.',
                    'The assessment text is not valid JSON.'),
    'duplicate_key': ('В JSON заключения повторяется имя поля.',
                      'The assessment JSON contains a duplicate field name.'),
    'output_size': ('Текст заключения превышает допустимый размер.',
                    'The assessment text exceeds the size limit.'),
    'output_type': ('Заключение должно быть JSON-объектом.',
                    'The assessment must be a JSON object.'),
    'output_fields': ('Набор полей заключения не соответствует схеме диагностики.',
                      'The assessment fields do not match the diagnostic schema.'),
    'status_value': ('В заключении указан недопустимый статус устройства.',
                     'The assessment contains an unsupported device status.'),
    'text_value': ('Текстовое поле заключения пустое, слишком длинное или имеет неверный тип.',
                   'An assessment text field is empty, too long or has an invalid type.'),
    'list_value': ('Список в заключении имеет неверный тип или превышает допустимое число записей.',
                   'An assessment list has an invalid type or contains too many entries.'),
    'evidence_shape': ('Запись свидетельства не соответствует схеме диагностики.',
                       'An evidence entry does not match the diagnostic schema.'),
    'evidence_source': ('Свидетельство ссылается на неподдерживаемый источник.',
                        'An evidence entry refers to an unsupported source.'),
    'log_reference_format': ('Ссылка на строку лога имеет неверный формат.',
                             'A log-line reference has an invalid format.'),
    'log_reference_missing': ('Заключение ссылается на строки лога, которые не передавались модели.',
                              'The assessment cites log lines that were not sent to the model.'),
    'snapshot_reference_format': ('Ссылка на показания устройства имеет неверный формат.',
                                  'A device-snapshot reference has an invalid format.'),
    'snapshot_reference_missing': ('Заключение ссылается на показания устройства, которые не передавались модели.',
                                   'The assessment cites device readings that were not sent to the model.'),
    'cause_shape': ('Описание возможной причины не соответствует схеме диагностики.',
                    'A likely-cause entry does not match the diagnostic schema.'),
    'confidence_value': ('Для возможной причины указан недопустимый уровень уверенности.',
                         'A likely-cause entry has an unsupported confidence level.'),
    'evidence_required': ('В заключении о состоянии устройства нет подтверждающих свидетельств.',
                          'The device assessment contains no supporting evidence.'),
    'output_structure': ('Структуру заключения не удалось проверить по схеме диагностики.',
                         'The assessment structure could not be validated against the diagnostic schema.'),
}

VALIDATION_STAGES = {
    'stream': ('поток ответа', 'response stream'),
    'output': ('формат заключения', 'assessment format'),
    'evidence': ('проверка свидетельств', 'evidence validation'),
}


def error_text(code, language, provider='yandex', *, details=None):
    message = CHATGPT_ERRORS.get(code) if provider == 'chatgpt' else None
    text = (message or ERRORS.get(code, ERRORS['error']))[language == 'en']
    # Reuse the contract allowlist even when a caller supplies a plain mapping.
    # Response messages, bodies, credentials and unknown fields never reach UI.
    public = AnalysisError('error', details=details).details
    if code == 'invalid_response':
        reason = VALIDATION_REASONS.get(public.get('validation_reason'))
        if reason:
            text = reason[language == 'en'] + (' No assessment was accepted.' if language == 'en'
                                             else ' Заключение не принято.')
        validation = []
        stage = VALIDATION_STAGES.get(public.get('validation_stage'))
        if stage:
            validation.append(('Stage: ' if language == 'en' else 'Этап: ') + stage[language == 'en'])
        if 'validation_field' in public:
            validation.append(('Field: ' if language == 'en' else 'Поле: ') + public['validation_field'])
        if validation:
            text += '\n' + ' · '.join(validation)
    fields = []
    if 'http_status' in public:
        fields.append('HTTP ' + str(public['http_status']))
    if 'provider_code' in public:
        fields.append(('Code: ' if language == 'en' else 'Код: ') + public['provider_code'])
    if 'provider_param' in public:
        fields.append(('Parameter: ' if language == 'en' else 'Параметр: ') + public['provider_param'])
    if fields:
        text += '\n' + ' · '.join(fields)
    if 'request_id' in public:
        text += '\nRequest ID: ' + public['request_id']
    return text


class _AnalysisWorker(QThread):
    def __init__(self, prepared, config, key, parent, *, chatgpt_store=None):
        super().__init__(parent)
        self.prepared, self.config = prepared, config
        self._key = key
        self.cancel = Event()
        self.report = None
        self.error_code = ''
        self.error_details = {}
        self.chatgpt_store = chatgpt_store

    def run(self):
        try:
            extra = {'chatgpt_store': self.chatgpt_store} if self.config.provider == 'chatgpt' else {}
            self.report = analyze(self.prepared, self.config, self._key, cancel=self.cancel, **extra)
        except AnalysisError as exc:
            self.error_code = exc.code
            self.error_details = getattr(exc, 'details', {})
        except Exception:
            self.error_code = 'error'
        finally:
            self._key = ''


class AIAnalysisPanel(QWidget):
    idle = pyqtSignal()
    activity_changed = pyqtSignal()

    def __init__(self, *, config=None, store=None, chatgpt_store=None, language='ru', parent=None):
        super().__init__(parent)
        self.language = language
        self.config = AnalysisConfig.from_mapping(config or {})
        self.store = store or AiCredentialStore(data_directory() / 'ai_credentials.dat')
        self.chatgpt_store = chatgpt_store or ChatGPTStore(data_directory() / 'chatgpt_credentials.dat')
        self.prepared = None
        self.report = None
        self._worker = None
        self._revision = 0
        self._worker_revision = None
        self._key_ready = False
        self._account_id = None
        self._worker_account_id = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 16, 12, 12)
        layout.setSpacing(10)
        self.hint = QLabel()
        self.hint.setTextFormat(Qt.TextFormat.PlainText)
        self.hint.setWordWrap(True)
        self.hint.setObjectName('Muted')
        layout.addWidget(self.hint)
        self.connection_summary = QLabel()
        self.connection_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.connection_summary.setWordWrap(True)
        self.connection_summary.setObjectName('Muted')
        layout.addWidget(self.connection_summary)
        self.usage_button = QPushButton(self._t('manage_usage'))
        self.usage_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl('https://chatgpt.com/settings/usage')))
        layout.addWidget(self.usage_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.status = QLabel(self._t('empty'))
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.pages = QTabWidget()
        self.preview = self._editor()
        self.result = self._editor()
        self.result.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.preview.setAccessibleName(self._t('preview'))
        self.result.setAccessibleName(self._t('result'))
        self.pages.addTab(self.preview, self._t('preview'))
        self.pages.addTab(self.result, self._t('result'))
        layout.addWidget(self.pages, 1)
        self.consent = QCheckBox(self._t('consent'))
        layout.addWidget(self.consent)
        self.consent.toggled.connect(self._update_controls)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        layout.addWidget(self.progress)
        controls = QHBoxLayout()
        self.analyze_button = QPushButton(self._t('analyze'))
        self.analyze_button.setProperty('primary', True)
        self.analyze_button.clicked.connect(self.start_analysis)
        self.cancel_button = QPushButton(self._t('cancel'))
        self.cancel_button.clicked.connect(self.request_stop)
        controls.addWidget(self.analyze_button)
        controls.addWidget(self.cancel_button)
        controls.addStretch()
        self.copy_button = QPushButton(self._t('copy'))
        self.copy_button.clicked.connect(self.copy_report)
        self.save_button = QPushButton(self._t('save'))
        self.save_button.clicked.connect(self.save_report)
        controls.addWidget(self.copy_button)
        controls.addWidget(self.save_button)
        layout.addLayout(controls)
        self._refresh_connection()
        self._update_controls()

    def _t(self, key):
        return TEXT[key][self.language == 'en']

    @staticmethod
    def _editor():
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        return editor

    @property
    def active(self):
        return self._worker is not None

    def set_config(self, config):
        self._revision += 1
        self.request_stop()
        self.config = AnalysisConfig.from_mapping(config or {})
        self.report = None
        self.result.clear()
        self.consent.setChecked(False)
        self._refresh_connection()
        self.status.setText(self._t('chatgpt_cancelling' if self.config.provider == 'chatgpt' else 'cancelling')
                            if self.active else self._ready_status())
        self._update_controls()

    def _ready_status(self):
        if self.prepared is None:
            return self._t('empty')
        text = self._t('ready').format(included=self.prepared.included_lines,
            omitted=self.prepared.omitted_lines, model=self.config.model or '—')
        if not self._connection_ready():
            text += '\n' + self._t('chatgpt_setup' if self.config.provider == 'chatgpt' else 'setup')
        return text

    def _refresh_connection(self):
        chatgpt = self.config.provider == 'chatgpt'
        self.hint.setText(self._t('chatgpt_hint' if chatgpt else 'hint'))
        self.consent.setText(self._t('chatgpt_consent' if chatgpt else 'consent'))
        self.usage_button.setVisible(chatgpt)
        if chatgpt:
            account = self._active_chatgpt_account()
            self._account_id = account.get('client_id') if account else None
            self._key_ready = bool(account and account.get('enabled'))
            name = (account.get('email') or account.get('subject') or '') if account else ''
            if self._account_id:
                name += ' · ' + self._account_id[-8:]
            self.connection_summary.setText(self._t('chatgpt_connection').format(
                account=name or '—', model=self.config.model or '—',
                permission=self._t('enabled' if self._key_ready else 'disabled')))
            return
        self._account_id = None
        try:
            self._key_ready = bool(self.store.load())
        except Exception:
            self._key_ready = False
        self.connection_summary.setText(self._t('connection').format(
            folder=self.config.folder_id or '—', model=self.config.model,
            key=self._t('configured' if self._key_ready else 'not_configured')))

    def _active_chatgpt_account(self):
        try:
            session = self.chatgpt_store.load()
            if not isinstance(session, dict):
                return None
            return next((item for item in self.chatgpt_store.accounts()
                         if item.get('client_id') == session.get('client_id')), None)
        except Exception:
            return None

    def _connection_ready(self):
        return self._key_ready and bool(self.config.model) and (
            self.config.provider == 'chatgpt' or bool(self.config.folder_id))

    def refresh_session(self):
        if self.config.provider != 'chatgpt':
            return
        self._revision += 1
        self.request_stop()
        self.report = None
        self.result.clear()
        self.consent.setChecked(False)
        self._refresh_connection()
        self.status.setText(self._t('chatgpt_cancelling') if self.active else self._ready_status())
        self._update_controls()

    def invalidate(self):
        self._revision += 1
        self.request_stop()
        self.prepared = self.report = None
        self.preview.clear()
        self.result.clear()
        self.consent.setChecked(False)
        self.status.setText(self._t('empty'))
        self._update_controls()

    def set_bundle(self, bundle):
        self.invalidate()
        self._refresh_connection()
        if not bundle:
            return
        try:
            self.prepared = prepare_analysis(bundle, language=self.language)
            self.preview.setPlainText(self.prepared.preview)
            self.status.setText(self._t('chatgpt_cancelling' if self.config.provider == 'chatgpt' else 'cancelling')
                                if self.active else self._ready_status())
        except Exception:
            self.prepared = None
            self.status.setText(self._t('preparation'))
        self._update_controls()

    def _update_controls(self):
        ready = self.prepared is not None and self._connection_ready()
        self.consent.setEnabled(ready and not self.active)
        self.analyze_button.setEnabled(ready and self.consent.isChecked() and not self.active)
        self.cancel_button.setEnabled(self.active and not self._worker.cancel.is_set())
        self.copy_button.setEnabled(self.report is not None and not self.active)
        self.save_button.setEnabled(self.report is not None and not self.active)

    def start_analysis(self):
        if self.active or self.prepared is None or not self.consent.isChecked():
            return
        key = ''
        if self.config.provider == 'chatgpt':
            account = self._active_chatgpt_account()
            if not account or not account.get('enabled') or not self.config.model:
                self.status.setText(self._t('chatgpt_setup'))
                self.consent.setChecked(False)
                return
            if account['client_id'] != self._account_id:
                self.refresh_session()
                return
            self._account_id = account['client_id']
        else:
            if not self.config.folder_id:
                self.status.setText(self._t('setup'))
                return
            try:
                key = self.store.load()
            except Exception:
                self.status.setText(self._t('credentials'))
                return
            if not key:
                self.status.setText(self._t('setup'))
                return
        self.report = None
        self.result.clear()
        self.status.setText(self._t('running'))
        self._worker_revision = self._revision
        self._worker_account_id = self._account_id
        self._worker = _AnalysisWorker(self.prepared, self.config, key, self,
                                      chatgpt_store=self.chatgpt_store)
        self._worker.finished.connect(self._finish)
        # Each transmission requires a fresh explicit choice, including retries.
        self.consent.setChecked(False)
        self.progress.show()
        self._update_controls()
        self.activity_changed.emit()
        self._worker.start()

    def request_stop(self):
        if self._worker is not None:
            self._worker.cancel.set()
            self.status.setText(self._t('chatgpt_cancelling' if self.config.provider == 'chatgpt' else 'cancelling'))
            self._update_controls()

    def _finish(self):
        worker = self._worker
        if worker is None:
            return
        self._worker = None
        self.progress.hide()
        self._refresh_connection()
        if worker.config.provider == 'chatgpt' and self._worker_account_id != self._account_id:
            self._revision += 1
        if self._worker_revision == self._revision:
            if worker.cancel.is_set():
                self.status.setText(self._t('cancelled'))
            elif worker.report is not None:
                self.report = worker.report
                self.result.setPlainText(self.report.display_text(language=self.language))
                self.pages.setCurrentIndex(1)
                self.status.setText(self._t('done'))
            else:
                self.status.setText(error_text(worker.error_code, self.language, worker.config.provider,
                                               details=worker.error_details))
                if worker.error_code in ('chatgpt_usage_limit', 'chatgpt_usage_unavailable', 'quota'):
                    self.usage_button.setProperty('primary', True)
                    self.usage_button.style().unpolish(self.usage_button)
                    self.usage_button.style().polish(self.usage_button)
        else:
            self.status.setText(self._ready_status())
        worker.deleteLater()
        self._update_controls()
        self.activity_changed.emit()
        self.idle.emit()

    def copy_report(self):
        if self.report is not None and not self.active:
            QApplication.clipboard().setText(self.result.toPlainText())
            self.status.setText(self._t('copied'))

    def save_report(self):
        if self.report is None or self.active:
            return
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        path, _ = QFileDialog.getSaveFileName(self, self._t('save'),
            f'asic-ai-assessment-{stamp}.txt', 'Text (*.txt)')
        if not path:
            return
        if not path.lower().endswith('.txt'):
            path += '.txt'
        try:
            with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
                stream.write(self.result.toPlainText())
        except OSError:
            self.status.setText(self._t('save_error'))
        else:
            self.status.setText(self._t('saved'))
