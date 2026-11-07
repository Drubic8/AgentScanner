"""Yandex configuration editor; credentials are committed only with dialog Save."""
from threading import Event

from PyQt6.QtCore import Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from miner_scanner.ai import AnalysisConfig, AnalysisError
from miner_scanner.ai.yandex import YandexProvider
from .ai_panel import error_text
from .ai_credentials import AiCredentialStore
from .chatgpt_store import ChatGPTStore
from .preferences import data_directory


TEXT = {
    'title': ('ИИ и диагностика', 'AI and diagnostics'),
    'hint': ('Выберите подключение для анализа ASIC. Перед каждым запросом программа покажет очищенные данные и попросит разрешение на отправку.',
             'Choose a connection for ASIC analysis. Review the redacted data and approve each request before sending.'),
    'provider': ('Провайдер', 'Provider'), 'folder': ('Каталог Yandex Cloud', 'Yandex Cloud folder'),
    'model': ('Модель', 'Model'), 'key': ('API-ключ', 'API key'),
    'key_existing': ('Ключ сохранён. Оставьте поле пустым, чтобы сохранить его.',
                     'A key is saved. Leave this field empty to keep it.'),
    'key_missing': ('Введите секретную часть API-ключа.', 'Enter the secret part of your API key.'),
    'remove': ('Удалить сохранённый ключ при сохранении', 'Delete the saved key when saving'),
    'timeout': ('Ожидание ответа, с', 'Request timeout, s'),
    'tokens': ('Лимит токенов ответа', 'Response token limit'),
    'key_hint': ('Ключ хранится отдельно от app_settings.json, с защитой текущего пользователя Windows. При переносе программы на другой ПК введите ключ заново.',
                 'The key is stored separately from app_settings.json and protected for this Windows user. Enter it again when moving to another PC.'),
    'check': ('Проверить подключение', 'Check connection'),
    'cancel': ('Отменить', 'Cancel'),
    'test_hint': ('Проверка отправляет короткий тестовый запрос без данных ASIC. Он может учитываться в оплате. Логи автоматически не отправляются.',
                  'The check sends a short test request without ASIC data. It may incur a charge. No logs are sent automatically.'),
    'testing': ('Проверяем подключение к Яндекс AI Studio…', 'Checking Yandex AI Studio connection…'),
    'testing_chatgpt': ('Проверяем запрос через ваш план ChatGPT…', 'Checking a request with your ChatGPT plan…'),
    'success': ('Подключение работает. Можно открыть карточку ASIC и выполнить анализ.',
                'Connection works. Open an ASIC card to run analysis.'),
    'missing': ('Укажите каталог и API-ключ.', 'Enter a folder ID and API key.'),
    'configuration': ('Проверьте идентификатор каталога, имя модели и ограничения запроса.',
                      'Check the folder ID, model name and request limits.'),
    'storage': ('Не удалось сохранить защищённый ключ. Настройки не применены.',
                'Could not save the protected key. Settings were not applied.'),
    'unreadable': ('Сохранённый ключ не удалось расшифровать. Введите новый ключ или удалите старый.',
                   'The saved key could not be decrypted. Enter a new key or delete the old one.'),
    'help': ('Создать ключ и каталог', 'Create a key and folder'),
    'models': ('Доступные модели', 'Available models'),
    'chatgpt': ('Аккаунт ChatGPT', 'ChatGPT account'),
    'account': ('Аккаунт', 'Account'),
    'other_account': ('Другой аккаунт…', 'Another account…'),
    'sign_out': ('Выйти из ChatGPT', 'Sign out of ChatGPT'),
    'load_models': ('Загрузить модели аккаунта', 'Load account models'),
    'choose_model': ('Сначала загрузите доступные модели', 'Load available models first'),
    'no_account': ('Аккаунт не подключён.', 'No account is connected.'),
    'plan_enabled': ('Используется план ChatGPT. Доступные запросы учитываются в плане или кредитах этого аккаунта.',
                     'Using ChatGPT plan. Eligible requests use this account’s plan or credits.'),
    'plan_disabled': ('Сессия аккаунта не разрешает использование плана. Нажмите Continue with ChatGPT и предоставьте разрешение.',
                      'The account session does not enable ChatGPT plan usage. Continue with ChatGPT and grant permission.'),
    'session_hint': ('Вход, выбор аккаунта и выход сохраняются сразу, независимо от кнопки «Сохранить». Пароль вводится только в браузере на странице OpenAI.',
                     'Sign-in, account selection and sign-out take effect immediately, independently of Save. Enter your password only in the browser on the OpenAI page.'),
    'chatgpt_test_hint': ('Проверка отправляет короткий тестовый запрос без данных ASIC и расходует лимит плана ChatGPT. Загрузка списка моделей не запускает анализ.',
                         'The check sends a short test request without ASIC data and uses your ChatGPT plan allowance. Loading model names does not run analysis.'),
    'chatgpt_limits': ('Лимит генерации определяет ChatGPT. Программа ограничивает время ожидания и размер получаемого ответа.',
                       'ChatGPT determines generation limits. The app bounds waiting time and the received response size.'),
    'chatgpt_missing': ('Войдите в ChatGPT, разрешите использование плана и выберите доступную модель.',
                        'Sign in to ChatGPT, allow plan usage and select an available model.'),
    'signing_in': ('Завершите вход и разрешение доступа в браузере. Ожидание до 3 минут.',
                   'Complete sign-in and consent in your browser. Allow up to 3 minutes.'),
    'auth_preparing': ('Подготавливаем вход ChatGPT на этом компьютере…',
                       'Preparing ChatGPT sign-in on this computer…'),
    'auth_browser': ('Страница входа передана браузеру. Завершите вход и разрешение доступа. Если окно не появилось, нажмите «Открыть окно входа».',
                     'The sign-in page was sent to your browser. Complete sign-in and consent. If no window appeared, use Open sign-in window.'),
    'auth_validating': ('Ответ браузера получен. Проверяем подключение и разрешение плана ChatGPT…',
                        'Browser response received. Verifying the connection and ChatGPT plan permission…'),
    'auth_reopen': ('Открыть окно входа', 'Open sign-in window'),
    'auth_browser_retry': ('Браузер не открыл окно входа. Нажмите «Открыть окно входа», пока эта попытка ожидает подтверждение.',
                           'The browser did not open the sign-in window. Use Open sign-in window while this attempt is waiting for consent.'),
    'signing_out': ('Завершаем сессию ChatGPT…', 'Signing out of ChatGPT…'),
    'models_loading': ('Получаем доступные модели аккаунта…', 'Loading available account models…'),
    'models_loaded': ('Список моделей загружен. Выберите модель и сохраните настройки.',
                      'Models loaded. Select a model and save settings.'),
    'signed_in': ('Аккаунт подключён. Загрузите модели аккаунта и выберите нужную.',
                  'Account connected. Load the account models and select one.'),
    'signed_out': ('Вы вышли из ChatGPT на этом компьютере.', 'Signed out of ChatGPT on this computer.'),
    'local_sign_out': ('Локальная сессия удалена. Отзыв доступа на сервере не подтверждён; проверьте подключения в настройках ChatGPT.',
                       'Local session removed. Remote revocation was not confirmed; review connections in ChatGPT settings.'),
    'session_error': ('Не удалось прочитать или сохранить защищённую сессию ChatGPT.',
                      'Could not read or save the protected ChatGPT session.'),
    'manage_usage': ('Управление использованием', 'Manage usage'),
    'welcome_title': ('Вы используете свой план ChatGPT', 'You’re using your ChatGPT plan'),
    'welcome_body': ('Допустимые запросы ASIC Monitor используют ваш план ChatGPT или доступные кредиты. Использованием и доступом приложения можно управлять в настройках ChatGPT.',
                     'Eligible requests in ASIC Monitor use your ChatGPT plan or available credits. Manage app usage and access in ChatGPT settings.'),
    'got_it': ('Понятно', 'Got it'),
}


class _ConnectionWorker(QThread):
    def __init__(self, config, key, parent):
        super().__init__(parent)
        self.config, self._key = config, key
        self.cancel = Event()
        self.error_code = ''
        self.error_details = {}
        self.kind = 'yandex_check'

    def run(self):
        try:
            YandexProvider(self.config, self._key).check_connection(cancel=self.cancel)
        except AnalysisError as exc:
            self.error_code = exc.code
            self.error_details = getattr(exc, 'details', {})
        except Exception:
            self.error_code = 'error'
        finally:
            self._key = ''


class _ChatGPTWorker(QThread):
    browser_requested = pyqtSignal(object, str)
    phase_changed = pyqtSignal(object, str)

    def __init__(self, kind, store, config, parent, *, new_account=False):
        super().__init__(parent)
        self.kind, self.store, self.config = kind, store, config
        self.new_account = new_account
        self.cancel = Event()
        self.error_code = ''
        self.error_details = {}
        self.result = None
        self.browser_error = False

    def run(self):
        try:
            if self.kind == 'sign_in':
                from miner_scanner.ai.chatgpt_auth import sign_in
                sign_in(self.store, open_browser=lambda url: self.browser_requested.emit(self, url),
                        cancel=self.cancel, timeout_seconds=180, new_account=self.new_account,
                        on_phase=lambda phase: self.phase_changed.emit(self, phase))
            elif self.kind == 'sign_out':
                from miner_scanner.ai.chatgpt_auth import sign_out
                self.result = sign_out(self.store, cancel=self.cancel)
            else:
                from miner_scanner.ai.openai import OpenAIProvider
                provider = OpenAIProvider(self.config, self.store)
                if self.kind == 'models':
                    self.result = provider.list_models(cancel=self.cancel)
                else:
                    provider.check_connection(cancel=self.cancel)
        except AnalysisError as exc:
            self.error_code = exc.code
            self.error_details = getattr(exc, 'details', {})
        except Exception:
            self.error_code = 'error'


class AISettingsPanel(QWidget):
    idle = pyqtSignal()
    session_changed = pyqtSignal()

    def __init__(self, settings, *, store=None, chatgpt_store=None, language='ru', parent=None):
        super().__init__(parent)
        self.language = language
        self.store = store or AiCredentialStore(data_directory() / 'ai_credentials.dat')
        self.chatgpt_store = chatgpt_store or ChatGPTStore(data_directory() / 'chatgpt_credentials.dat')
        self._worker = None
        self._authorization_url = None
        self._saved_key = ''
        self._key_read_error = False
        config = AnalysisConfig.from_mapping(settings or {})
        self._yandex_loaded = config.provider == 'yandex'
        if self._yandex_loaded:
            try:
                self._saved_key = self.store.load()
            except Exception:
                self._key_read_error = True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setSpacing(12)
        self.form = form
        self.provider = QComboBox()
        self.provider.addItem('Yandex AI Studio', 'yandex')
        self.provider.addItem(self._t('chatgpt'), 'chatgpt')
        self.provider.setCurrentIndex(max(0, self.provider.findData(config.provider)))
        form.addRow(self._t('provider'), self.provider)
        self.folder = QLineEdit(config.folder_id)
        self.folder.setPlaceholderText('b1g…')
        self.folder.setMaxLength(64)
        self.folder.setAccessibleName(self._t('folder'))
        form.addRow(self._t('folder'), self.folder)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setMaxVisibleItems(6)
        self.model.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.model.setMinimumContentsLength(18)
        self.model.addItems(['yandexgpt-5.1', 'aliceai-llm', 'aliceai-llm-flash'])
        self.model.setCurrentText(config.model if config.provider == 'yandex' else 'yandexgpt-5.1')
        self.model.setAccessibleName(self._t('model'))
        form.addRow(self._t('model'), self.model)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setMaxLength(4096)
        self.key.setPlaceholderText(self._t('key_existing' if self._saved_key else 'key_missing'))
        self.key.setAccessibleName(self._t('key'))
        form.addRow(self._t('key'), self.key)
        self.remove_key = QCheckBox(self._t('remove'))
        self.remove_key.toggled.connect(lambda checked: self.key.setEnabled(not checked and not self.active))
        form.addRow('', self.remove_key)
        self.timeout = QSpinBox()
        self.timeout.setRange(5, 120)
        self.timeout.setValue(config.timeout_seconds)
        form.addRow(self._t('timeout'), self.timeout)
        self.tokens = QSpinBox()
        self.tokens.setRange(64, 4096)
        self.tokens.setSingleStep(128)
        self.tokens.setValue(config.max_output_tokens)
        form.addRow(self._t('tokens'), self.tokens)
        self._chatgpt_page(config)
        layout.addLayout(form)
        self.key_hint = self._label(self._t('key_hint'))
        self.key_hint.setObjectName('Muted')
        layout.addWidget(self.key_hint)
        links = QHBoxLayout()
        self.help_button = QPushButton(self._t('help'))
        self.help_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(
            'https://aistudio.yandex.ru/ru/docs/ai-studio/operations/get-api-key')))
        self.models_button = QPushButton(self._t('models'))
        self.models_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(
            'https://aistudio.yandex.ru/ru/docs/ai-studio/concepts/generation/models')))
        links.addWidget(self.help_button)
        links.addWidget(self.models_button)
        links.addStretch()
        layout.addLayout(links)
        controls = QHBoxLayout()
        self.check_button = QPushButton(self._t('check'))
        self.check_button.clicked.connect(self.check_connection)
        self.cancel_button = QPushButton(self._t('cancel'))
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.request_stop)
        controls.addWidget(self.check_button)
        controls.addWidget(self.cancel_button)
        controls.addStretch()
        layout.addLayout(controls)
        self.test_hint = self._label(self._t('test_hint'))
        self.test_hint.setObjectName('Muted')
        layout.addWidget(self.test_hint)
        self.status = self._label(self._t('unreadable') if self._key_read_error else '')
        layout.addWidget(self.status)
        layout.addStretch()
        self.provider.currentIndexChanged.connect(self._provider_changed)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        self._set_provider_mode()

    def _t(self, key):
        return TEXT[key][self.language == 'en']

    @staticmethod
    def _label(text):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        return label

    def _chatgpt_page(self, config):
        self.chatgpt_body = QWidget()
        layout = QVBoxLayout(self.chatgpt_body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.accounts = QComboBox()
        self.accounts.setPlaceholderText(self._t('no_account'))
        self.accounts.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.accounts.setMinimumContentsLength(20)
        self.accounts.setMaxVisibleItems(6)
        form.addRow(self._t('account'), self.accounts)
        self.chatgpt_model = QComboBox()
        self.chatgpt_model.setMaxVisibleItems(8)
        self.chatgpt_model.setPlaceholderText(self._t('choose_model'))
        self.chatgpt_model.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.chatgpt_model.setMinimumContentsLength(20)
        if config.provider == 'chatgpt' and config.model:
            self.chatgpt_model.addItem(config.model, config.model)
            self.chatgpt_model.setCurrentIndex(self.chatgpt_model.findData(config.model))
        form.addRow(self._t('model'), self.chatgpt_model)
        layout.addLayout(form)
        self.account_status = self._label('')
        layout.addWidget(self.account_status)
        limits = self._label(self._t('chatgpt_limits'))
        limits.setObjectName('Muted')
        layout.addWidget(limits)
        actions = QHBoxLayout()
        self.sign_in_button = QPushButton('Continue with ChatGPT')
        self.sign_in_button.clicked.connect(lambda: self.sign_in())
        self.other_account_button = QPushButton(self._t('other_account'))
        self.other_account_button.clicked.connect(lambda: self.sign_in(new_account=True))
        self.sign_out_button = QPushButton(self._t('sign_out'))
        self.sign_out_button.clicked.connect(self.sign_out)
        actions.addWidget(self.sign_in_button)
        actions.addWidget(self.other_account_button)
        actions.addWidget(self.sign_out_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.auth_status = self._label('')
        self.auth_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.auth_status)
        self.auth_progress = QProgressBar()
        self.auth_progress.setRange(0, 0)
        self.auth_progress.setTextVisible(False)
        self.auth_progress.hide()
        layout.addWidget(self.auth_progress)
        self.open_browser_button = QPushButton(self._t('auth_reopen'))
        self.open_browser_button.clicked.connect(self._reopen_browser)
        self.open_browser_button.hide()
        layout.addWidget(self.open_browser_button, alignment=Qt.AlignmentFlag.AlignLeft)
        actions = QHBoxLayout()
        self.load_models_button = QPushButton(self._t('load_models'))
        self.load_models_button.clicked.connect(self.load_models)
        self.manage_usage_button = QPushButton(self._t('manage_usage'))
        self.manage_usage_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl('https://chatgpt.com/settings/usage')))
        actions.addWidget(self.load_models_button)
        actions.addWidget(self.manage_usage_button)
        actions.addStretch()
        layout.addLayout(actions)
        hint = self._label(self._t('session_hint'))
        hint.setObjectName('Muted')
        layout.addWidget(hint)
        self.form.insertRow(1, self.chatgpt_body)
        self._refresh_accounts()
        self.accounts.currentIndexChanged.connect(self._account_selected)
        self.chatgpt_model.currentIndexChanged.connect(self._model_selected)

    def _active_account(self):
        try:
            session = self.chatgpt_store.load()
            if not isinstance(session, dict):
                return None
            client_id = session.get('client_id')
            return next((item for item in self.chatgpt_store.accounts()
                         if item.get('client_id') == client_id), None)
        except Exception:
            return None

    def _refresh_accounts(self):
        self.accounts.blockSignals(True)
        self.accounts.clear()
        try:
            records = self.chatgpt_store.accounts()
        except Exception:
            records = []
        active = self._active_account()
        active_id = active.get('client_id') if active else None
        for item in records:
            client_id = item.get('client_id')
            if not isinstance(client_id, str):
                continue
            name = item.get('email') or item.get('subject') or self._t('account')
            self.accounts.addItem(str(name) + ' · ' + client_id[-8:], client_id)
        self.accounts.setCurrentIndex(self.accounts.findData(active_id))
        self.accounts.blockSignals(False)
        self.account_status.setText(self._t('plan_enabled' if active and active.get('enabled') else
            'plan_disabled' if active else 'no_account'))
        self.sign_out_button.setEnabled(bool(active) and not self.active)
        self.load_models_button.setEnabled(bool(active and active.get('enabled')) and not self.active)
        self.other_account_button.setVisible(bool(records))
        if hasattr(self, 'check_button') and self.provider.currentData() == 'chatgpt':
            self.check_button.setEnabled(bool(active and active.get('enabled') and self.chatgpt_model.currentData())
                                         and not self.active)

    def _model_selected(self):
        if hasattr(self, 'check_button') and self.provider.currentData() == 'chatgpt':
            account = self._active_account()
            self.check_button.setEnabled(bool(account and account.get('enabled') and self.chatgpt_model.currentData())
                                         and not self.active)

    def _provider_changed(self):
        self.session_changed.emit()
        self.status.clear()
        self.auth_status.clear()
        self._set_provider_mode()

    def _set_provider_mode(self):
        chatgpt = self.provider.currentData() == 'chatgpt'
        if not chatgpt and not self._yandex_loaded:
            self._yandex_loaded = True
            try:
                self._saved_key = self.store.load()
            except Exception:
                self._key_read_error = True
            self.key.setPlaceholderText(self._t('key_existing' if self._saved_key else 'key_missing'))
        for field in (self.folder, self.model, self.key, self.remove_key, self.tokens):
            self.form.setRowVisible(field, not chatgpt)
        self.chatgpt_body.setVisible(chatgpt)
        self.key_hint.setVisible(not chatgpt)
        self.help_button.setVisible(not chatgpt)
        self.models_button.setVisible(not chatgpt)
        self.test_hint.setText(self._t('chatgpt_test_hint' if chatgpt else 'test_hint'))
        self.status.setVisible(not chatgpt)
        if chatgpt:
            self._refresh_accounts()
        else:
            self.check_button.setEnabled(not self.active)

    def _account_selected(self):
        if self.active:
            return
        client_id = self.accounts.currentData()
        if not client_id:
            return
        self.session_changed.emit()
        try:
            self.chatgpt_store.select(client_id)
        except Exception:
            self._set_status(self._t('session_error'))
        else:
            self.chatgpt_model.clear()
            self._set_status(self._t('signed_in'))
        self._refresh_accounts()

    def _start_chatgpt(self, kind, *, new_account=False):
        if self.active:
            return
        try:
            # Authentication and account model discovery must not validate
            # unrelated Yandex drafts or require an inference model first.
            config = (AnalysisConfig.from_mapping(dict(provider='chatgpt', model='',
                timeout_seconds=self.timeout.value())) if kind in ('sign_in', 'sign_out', 'models')
                else self.configuration())
        except (ValueError, TypeError):
            self._set_status(self._t('configuration'))
            return
        if kind in ('models', 'chatgpt_check'):
            account = self._active_account()
            if not account or not account.get('enabled') or (kind == 'chatgpt_check' and not config.model):
                self._set_status(self._t('chatgpt_missing'))
                return
        if kind in ('sign_in', 'sign_out'):
            self.session_changed.emit()
        self._clear_authorization_url()
        self._set_status(self._t({'sign_in': 'auth_preparing', 'sign_out': 'signing_out',
            'models': 'models_loading', 'chatgpt_check': 'testing_chatgpt'}[kind]))
        self._worker = _ChatGPTWorker(kind, self.chatgpt_store, config, self, new_account=new_account)
        self._worker.browser_requested.connect(self._open_browser, Qt.ConnectionType.QueuedConnection)
        self._worker.phase_changed.connect(self._show_auth_phase, Qt.ConnectionType.QueuedConnection)
        self._worker.finished.connect(self._finish)
        self._set_busy(True)
        self._worker.start()

    def _set_status(self, text):
        self.status.setText(text)
        self.auth_status.setText(text if self.provider.currentData() == 'chatgpt' else '')

    def _clear_authorization_url(self):
        self._authorization_url = None
        self.open_browser_button.hide()
        self.open_browser_button.setEnabled(False)

    def _show_auth_phase(self, worker, phase):
        if worker is not self._worker or worker.cancel.is_set():
            return
        if phase == 'validating':
            self._clear_authorization_url()
            worker.browser_error = False
        key = {'preparing': 'auth_preparing', 'waiting_browser': 'auth_browser',
               'validating': 'auth_validating'}.get(phase)
        if key:
            if phase == 'waiting_browser' and worker.browser_error:
                key = 'auth_browser_retry'
            self._set_status(self._t(key))

    def _open_browser(self, worker, url):
        # Queued to this widget's GUI thread; authorization URLs are never logged.
        if worker is not self._worker or worker.cancel.is_set() or worker.kind != 'sign_in':
            return
        parsed = QUrl(url)
        if (parsed.scheme() != 'https' or parsed.host() != 'auth.openai.com' or
                parsed.userName() or parsed.password() or parsed.port(-1) not in (-1, 443)):
            worker.browser_error = True
            worker.cancel.set()
            self._clear_authorization_url()
            self._set_status(error_text('browser_failed', self.language, 'chatgpt'))
            return
        self._authorization_url = url
        self.open_browser_button.show()
        self.open_browser_button.setEnabled(True)
        self._launch_browser(worker, parsed)

    def _launch_browser(self, worker, parsed):
        if worker is not self._worker or worker.cancel.is_set():
            return
        if not QDesktopServices.openUrl(parsed):
            worker.browser_error = True
            self._set_status(self._t('auth_browser_retry'))
        else:
            worker.browser_error = False
            self._set_status(self._t('auth_browser'))

    def _reopen_browser(self):
        worker = self._worker
        if worker is not None and worker.kind == 'sign_in' and not worker.cancel.is_set() and self._authorization_url:
            self._launch_browser(worker, QUrl(self._authorization_url))

    def sign_in(self, *, new_account=False):
        self._start_chatgpt('sign_in', new_account=new_account)

    def sign_out(self):
        self._start_chatgpt('sign_out')

    def load_models(self):
        self._start_chatgpt('models')

    def _show_plan_welcome(self):
        account = self._active_account()
        if not account or not account.get('enabled'):
            return
        client_id = account['client_id']
        try:
            if self.chatgpt_store.has_welcome(client_id):
                return
        except Exception:
            self._set_status(self._t('session_error'))
            return
        modal = QMessageBox(self)
        modal.setWindowTitle(self._t('welcome_title'))
        modal.setText(self._t('welcome_title'))
        modal.setInformativeText(self._t('welcome_body'))
        modal.setTextFormat(Qt.TextFormat.PlainText)
        modal.addButton(self._t('got_it'), QMessageBox.ButtonRole.AcceptRole)
        modal.exec()
        try:
            self.chatgpt_store.mark_welcome(client_id)
        except Exception:
            self._set_status(self._t('session_error'))

    def _set_busy(self, busy):
        for field in (self.provider, self.folder, self.model, self.remove_key, self.timeout,
                      self.tokens, self.check_button, self.accounts, self.chatgpt_model,
                      self.sign_in_button, self.other_account_button, self.sign_out_button,
                      self.load_models_button, self.manage_usage_button):
            field.setEnabled(not busy)
        self.key.setEnabled(not busy and not self.remove_key.isChecked())
        self.cancel_button.setEnabled(busy)
        self.auth_progress.setVisible(busy and self.provider.currentData() == 'chatgpt')
        if not busy:
            self._refresh_accounts()

    @property
    def active(self):
        return self._worker is not None

    def configuration(self):
        provider = self.provider.currentData()
        model = self.chatgpt_model.currentData() or '' if provider == 'chatgpt' else self.model.currentText().strip()
        return AnalysisConfig.from_mapping(dict(provider=provider,
            folder_id=self.folder.text().strip() if provider == 'yandex' else '',
            model=model, timeout_seconds=self.timeout.value(),
            max_output_tokens=self.tokens.value()))

    def commit_credentials(self):
        if self.active:
            return False
        if self.provider.currentData() == 'chatgpt':
            return True
        try:
            if self.remove_key.isChecked():
                self.store.delete()
                self._saved_key = ''
            elif self.key.text():
                self.store.save(self.key.text())
                self._saved_key = self.key.text()
        except Exception:
            self._set_status(self._t('storage'))
            return False
        self.key.clear()
        return True

    def check_connection(self):
        if self.active:
            return
        if self.provider.currentData() == 'chatgpt':
            self._start_chatgpt('chatgpt_check')
            return
        try:
            config = self.configuration()
        except (ValueError, TypeError):
            self._set_status(self._t('configuration'))
            return
        key = '' if self.remove_key.isChecked() else self.key.text() or self._saved_key
        if not config.folder_id or not key:
            self._set_status(self._t('missing'))
            return
        self._set_status(self._t('testing'))
        self._worker = _ConnectionWorker(config, key, self)
        self._worker.finished.connect(self._finish)
        self._set_busy(True)
        self._worker.start()

    def request_stop(self):
        if self._worker is not None:
            self._worker.browser_error = False
            self._worker.cancel.set()
            self.cancel_button.setEnabled(False)
        self._clear_authorization_url()

    def _finish(self):
        worker = self._worker
        if worker is None:
            return
        self._worker = None
        self._clear_authorization_url()
        code = 'browser_failed' if getattr(worker, 'browser_error', False) else (
            'cancelled' if worker.cancel.is_set() else worker.error_code)
        provider = 'yandex' if worker.kind == 'yandex_check' else 'chatgpt'
        if code:
            self._set_status(error_text(code, self.language, provider,
                details=worker.error_details if code == worker.error_code else None))
            if provider == 'chatgpt' and code in ('chatgpt_usage_limit', 'chatgpt_usage_unavailable', 'quota'):
                self.manage_usage_button.setProperty('primary', True)
                self.manage_usage_button.style().unpolish(self.manage_usage_button)
                self.manage_usage_button.style().polish(self.manage_usage_button)
        elif worker.kind == 'models':
            previous = self.chatgpt_model.currentData()
            self.chatgpt_model.clear()
            for model in worker.result or []:
                self.chatgpt_model.addItem(model, model)
            self.chatgpt_model.setCurrentIndex(self.chatgpt_model.findData(previous))
            self._set_status(self._t('models_loaded'))
        elif worker.kind == 'sign_in':
            self.chatgpt_model.clear()
            self._set_status(self._t('signed_in'))
        elif worker.kind == 'sign_out':
            self.chatgpt_model.clear()
            self._set_status(self._t('signed_out' if worker.result else 'local_sign_out'))
        else:
            self._set_status(self._t('success'))
        self._set_busy(False)
        if worker.kind in ('sign_in', 'sign_out'):
            self.session_changed.emit()
        if worker.kind == 'sign_in' and not code:
            self._show_plan_welcome()
        worker.deleteLater()
        self.idle.emit()
