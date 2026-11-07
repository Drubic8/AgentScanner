"""Offline Qt checks for account sign-in, provider isolation and explicit consent."""
import importlib.util
import os
from threading import Event
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


class NoApiKeyStore:
    def __init__(self):
        self.reads = 0

    def load(self):
        self.reads += 1
        raise AssertionError('A ChatGPT account must not read a Yandex API key')

    def save(self, value):
        raise AssertionError('No Yandex key write on ChatGPT path')

    def delete(self):
        raise AssertionError('No Yandex key deletion on ChatGPT path')


class ChatGPTMemoryStore:
    def __init__(self, *, enabled=True, second=False, empty=False):
        self.records = [] if empty else [dict(client_id='client-one-12345678', email='demo@example.invalid',
            subject='subject-one', enabled=enabled)]
        if second:
            self.records.append(dict(client_id='client-two-87654321', email='demo@example.invalid',
                                     subject='subject-two', enabled=True))
        self.active_id = self.records[0]['client_id'] if self.records else None
        self.welcomed = set()

    def load(self):
        public = next((item for item in self.records if item['client_id'] == self.active_id), None)
        return dict(public, access_token='SECRET-ACCESS-TOKEN', refresh_token='SECRET-REFRESH-TOKEN') if public else None

    def accounts(self):
        return [dict(item) for item in self.records]

    def select(self, client_id):
        if client_id not in [item['client_id'] for item in self.records]:
            raise ValueError('Unknown account')
        self.active_id = client_id

    def clear_tokens(self, client_id=None):
        for item in self.records:
            if item['client_id'] == (client_id or self.active_id):
                item['enabled'] = False

    def has_welcome(self, client_id):
        return client_id in self.welcomed

    def mark_welcome(self, client_id):
        self.welcomed.add(client_id)


@unittest.skipUnless(importlib.util.find_spec('PyQt6'), 'Desktop dependencies not installed')
class ChatGPTDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        import desktop_ui.ai_panel as analysis
        import desktop_ui.ai_settings_panel as settings
        cls.analysis, cls.settings = analysis, settings
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from desktop_ui.i18n import set_language
        set_language('ru')
        self.addCleanup(set_language, 'ru')

    def wait(self, predicate, seconds=5):
        deadline = time.monotonic() + seconds
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.app.processEvents()
        self.assertTrue(predicate(), 'Qt account worker did not finish')

    def clean(self, panel):
        panel.request_stop()
        self.wait(lambda: not panel.active)
        panel.close()

    def settings_panel(self, store=None, **changes):
        panel = self.settings.AISettingsPanel({'provider': 'chatgpt'},
            store=NoApiKeyStore(), chatgpt_store=store or ChatGPTMemoryStore(), **changes)
        self.addCleanup(self.clean, panel)
        return panel

    def bundle(self):
        from miner_scanner.device_logs import DeviceLogResult
        from miner_scanner.diagnostic_bundle import build_bundle
        return build_bundle(dict(IP='192.0.2.2', Model='Demo ASIC', Real='100 TH/s'),
            DeviceLogResult('succeeded', text='temperature sensor failed', kind='miner',
                source='fixture', collected_at='2026-11-01T10:00:00+00:00'), app_version='test')

    def analysis_panel(self, store=None):
        panel = self.analysis.AIAnalysisPanel(config={'provider': 'chatgpt', 'model': 'account-model'},
            store=NoApiKeyStore(), chatgpt_store=store or ChatGPTMemoryStore())
        panel.set_bundle(self.bundle())
        self.addCleanup(self.clean, panel)
        return panel

    def test_opening_settings_does_not_authenticate_or_generate_and_has_no_api_key_field(self):
        with patch('miner_scanner.ai.chatgpt_auth.sign_in') as sign_in, patch('miner_scanner.ai.openai.OpenAIProvider') as provider:
            panel = self.settings_panel()
            sign_in.assert_not_called()
            provider.assert_not_called()
        self.assertEqual(panel.sign_in_button.text(), 'Continue with ChatGPT')
        self.assertEqual(panel.provider.currentData(), 'chatgpt')
        self.assertFalse(panel.form.isRowVisible(panel.key))
        self.assertFalse(panel.form.isRowVisible(panel.folder))
        self.assertFalse(panel.form.isRowVisible(panel.tokens))
        self.assertEqual(panel.configuration().model, '')
        self.assertEqual(panel.chatgpt_model.count(), 0)
        self.assertEqual(panel.store.reads, 0)

    def test_saved_chatgpt_model_is_kept_until_the_user_selects_another(self):
        panel = self.settings.AISettingsPanel({'provider': 'chatgpt', 'model': 'saved-account-model'},
            store=NoApiKeyStore(), chatgpt_store=ChatGPTMemoryStore())
        self.addCleanup(self.clean, panel)
        self.assertEqual(panel.chatgpt_model.currentData(), 'saved-account-model')
        self.assertEqual(panel.configuration().model, 'saved-account-model')

    def test_account_labels_are_distinct_and_tokens_do_not_enter_widgets_or_preferences(self):
        panel = self.settings_panel(ChatGPTMemoryStore(second=True))
        self.assertNotEqual(panel.accounts.itemText(0), panel.accounts.itemText(1))
        self.assertIn('12345678', panel.accounts.itemText(0))
        self.assertIn('87654321', panel.accounts.itemText(1))
        self.assertNotIn('SECRET', panel.account_status.text())
        self.assertNotIn('SECRET', str(panel.configuration().to_mapping()))

    def test_sign_in_opens_browser_only_on_gui_thread_and_preserves_new_account_flag(self):
        from PyQt6.QtCore import QThread
        store = ChatGPTMemoryStore()
        panel = self.settings_panel(store)
        calls = []

        def sign_in(target, *, open_browser, cancel, timeout_seconds, new_account, on_phase=None):
            calls.append((target, new_account, timeout_seconds))
            open_browser('https://auth.openai.com/oauth/authorize?state=fixture')

        threads = []
        with (patch('miner_scanner.ai.chatgpt_auth.sign_in', side_effect=sign_in),
              patch.object(panel, '_show_plan_welcome'),
              patch.object(self.settings.QDesktopServices, 'openUrl', side_effect=lambda url: threads.append(QThread.currentThread()) or True)):
            panel.sign_in(new_account=True)
            self.wait(lambda: not panel.active)
        self.assertEqual(calls, [(store, True, 180)])
        self.assertEqual(threads, [self.app.thread()])
        self.assertEqual(store.active_id, 'client-one-12345678')

    def test_finished_browser_open_failure_has_an_actionable_message(self):
        panel = self.settings_panel()

        def sign_in(target, *, open_browser, cancel, timeout_seconds, new_account, on_phase=None):
            open_browser('https://auth.openai.com/oauth/authorize?state=fixture')
            cancel.wait(2)

        with (patch('miner_scanner.ai.chatgpt_auth.sign_in', side_effect=sign_in),
              patch.object(self.settings.QDesktopServices, 'openUrl', return_value=False)):
            panel.sign_in()
            self.wait(lambda: not panel.active)
        self.assertIn('браузер', panel.status.text())

    def test_identity_only_account_cannot_load_models_or_generate(self):
        store = ChatGPTMemoryStore(enabled=False)
        panel = self.settings_panel(store)
        self.assertFalse(panel.load_models_button.isEnabled())
        with patch('miner_scanner.ai.openai.OpenAIProvider') as provider:
            panel.load_models()
            panel.check_connection()
            provider.assert_not_called()
        analysis = self.analysis_panel(store)
        self.assertFalse(analysis.consent.isEnabled())
        self.assertFalse(analysis.analyze_button.isEnabled())

    def test_models_are_loaded_by_explicit_read_and_user_selects_an_advertised_name(self):
        panel = self.settings_panel()
        with patch('miner_scanner.ai.openai.OpenAIProvider') as provider:
            provider.return_value.list_models.return_value = ['account-model-a', 'account-model-b']
            panel.load_models()
            self.wait(lambda: not panel.active)
            provider.return_value.list_models.assert_called_once()
            provider.return_value.check_connection.assert_not_called()
        self.assertEqual([panel.chatgpt_model.itemData(i) for i in range(panel.chatgpt_model.count())],
                         ['account-model-a', 'account-model-b'])
        self.assertEqual(panel.configuration().model, '')
        panel.chatgpt_model.setCurrentIndex(1)
        self.assertEqual(panel.configuration().model, 'account-model-b')

    def test_synthetic_check_is_explicit_and_uses_chatgpt_provider_only(self):
        panel = self.settings_panel()
        panel.chatgpt_model.addItem('account-model', 'account-model')
        panel.chatgpt_model.setCurrentIndex(0)
        with patch('miner_scanner.ai.openai.OpenAIProvider') as provider, patch.object(self.settings, 'YandexProvider') as yandex:
            panel.check_connection()
            self.wait(lambda: not panel.active)
            provider.return_value.check_connection.assert_called_once()
            yandex.assert_not_called()

    def test_welcome_is_shown_only_once_for_a_plan_enabled_registration(self):
        store = ChatGPTMemoryStore()
        panel = self.settings_panel(store)
        with patch.object(self.settings.QMessageBox, 'exec', return_value=0) as modal:
            panel._show_plan_welcome()
            panel._show_plan_welcome()
        modal.assert_called_once()
        self.assertTrue(store.has_welcome(store.active_id))

    def test_settings_cancel_keeps_explicit_account_selection(self):
        from desktop_ui.settings_dialog import SettingsDialog
        from desktop_ui.preferences import defaults
        settings = defaults()
        settings['ai'] = {'provider': 'chatgpt'}
        store = ChatGPTMemoryStore(second=True)
        dialog = SettingsDialog(settings, ai_store=NoApiKeyStore(), chatgpt_store=store)
        changed = []
        dialog.ai_session_changed.connect(lambda: changed.append(True))
        dialog.ai_panel.accounts.setCurrentIndex(1)
        dialog.reject()
        self.assertEqual(store.active_id, 'client-two-87654321')
        self.assertTrue(changed)

    def test_analysis_requires_consent_uses_account_store_and_does_not_read_api_key(self):
        panel = self.analysis_panel()
        report = SimpleNamespace(display_text=lambda language='ru': 'Account assessment')
        with patch.object(self.analysis, 'analyze', return_value=report) as analyze:
            panel.start_analysis()
            analyze.assert_not_called()
            panel.consent.setChecked(True)
            panel.start_analysis()
            self.wait(lambda: not panel.active)
        self.assertEqual(analyze.call_args.args[2], '')
        self.assertIs(analyze.call_args.kwargs['chatgpt_store'], panel.chatgpt_store)
        self.assertNotIn('Яндекс', panel.consent.text())
        self.assertIn('ChatGPT', panel.connection_summary.text())
        self.assertEqual(panel.result.toPlainText(), 'Account assessment')
        self.assertFalse(panel.consent.isChecked())
        self.assertEqual(panel.store.reads, 0)

    def test_account_change_after_preview_invalidates_consent_before_any_request(self):
        store = ChatGPTMemoryStore(second=True)
        panel = self.analysis_panel(store)
        panel.consent.setChecked(True)
        store.select('client-two-87654321')
        with patch.object(self.analysis, 'analyze') as analyze:
            panel.start_analysis()
            analyze.assert_not_called()
        self.assertFalse(panel.consent.isChecked())
        self.assertIn('87654321', panel.connection_summary.text())

    def test_switching_account_cancels_and_discards_late_assessment(self):
        store = ChatGPTMemoryStore(second=True)
        panel = self.analysis_panel(store)
        report = SimpleNamespace(display_text=lambda language='ru': 'Old account response')

        def late(prepared, config, key, *, cancel, chatgpt_store):
            cancel.wait(2)
            return report

        with patch.object(self.analysis, 'analyze', side_effect=late):
            panel.consent.setChecked(True)
            panel.start_analysis()
            store.select('client-two-87654321')
            panel.refresh_session()
            self.wait(lambda: not panel.active)
        self.assertIsNone(panel.report)
        self.assertEqual(panel.result.toPlainText(), '')
        self.assertFalse(panel.consent.isChecked())

    def test_signout_clears_local_access_and_reports_unconfirmed_remote_revocation(self):
        store = ChatGPTMemoryStore()
        panel = self.settings_panel(store)

        def sign_out(target, *, cancel):
            target.clear_tokens()
            return False

        with patch('miner_scanner.ai.chatgpt_auth.sign_out', side_effect=sign_out):
            panel.sign_out()
            self.wait(lambda: not panel.active)
        self.assertFalse(store.accounts()[0]['enabled'])
        self.assertIn('не подтверждён', panel.status.text())

    def test_closing_auth_dialog_waits_for_cancellation_without_destroying_worker(self):
        from desktop_ui.settings_dialog import SettingsDialog
        from desktop_ui.preferences import defaults
        from miner_scanner.ai import AnalysisError
        settings = defaults()
        settings['ai'] = {'provider': 'chatgpt'}
        dialog = SettingsDialog(settings, ai_store=NoApiKeyStore(), chatgpt_store=ChatGPTMemoryStore())

        def wait_sign_in(store, *, open_browser, cancel, timeout_seconds, new_account, on_phase=None):
            cancel.wait(2)
            raise AnalysisError('cancelled')

        with patch('miner_scanner.ai.chatgpt_auth.sign_in', side_effect=wait_sign_in):
            dialog.ai_panel.sign_in()
            self.assertTrue(dialog.ai_panel.active)
            dialog.reject()
            self.assertTrue(dialog.ai_panel.active)
            self.wait(lambda: not dialog.ai_panel.active)
        self.assertEqual(dialog.result(), 0)

    def test_chatgpt_limit_error_emphasizes_manage_usage(self):
        from miner_scanner.ai import AnalysisError
        panel = self.analysis_panel()
        with patch.object(self.analysis, 'analyze', side_effect=AnalysisError('chatgpt_usage_limit')):
            panel.consent.setChecked(True)
            panel.start_analysis()
            self.wait(lambda: not panel.active)
        self.assertIn('лимит', panel.status.text())
        self.assertTrue(panel.usage_button.property('primary'))
        self.assertIsNone(panel.report)

    def test_provider_switch_preserves_yandex_draft_without_reusing_key_for_chatgpt(self):
        from tests.test_ai_dialog import MemoryStore
        store = MemoryStore()
        panel = self.settings.AISettingsPanel({}, store=store, chatgpt_store=ChatGPTMemoryStore())
        self.addCleanup(self.clean, panel)
        panel.folder.setText('yandex-folder')
        panel.model.setCurrentText('custom-yandex-model')
        panel.key.setText('unsaved-yandex-key')
        panel.provider.setCurrentIndex(panel.provider.findData('chatgpt'))
        self.assertEqual(panel.configuration().provider, 'chatgpt')
        self.assertEqual(panel.configuration().model, '')
        self.assertTrue(panel.commit_credentials())
        self.assertEqual(store.saved, [])
        panel.provider.setCurrentIndex(panel.provider.findData('yandex'))
        self.assertEqual(panel.model.currentText(), 'custom-yandex-model')
        self.assertEqual(panel.folder.text(), 'yandex-folder')
        self.assertEqual(panel.key.text(), 'unsaved-yandex-key')

    def test_continue_button_shows_missing_components_next_to_auth_buttons(self):
        from desktop_ui.settings_dialog import SettingsDialog
        from desktop_ui.preferences import defaults
        from miner_scanner.ai import AnalysisError
        settings = defaults()
        settings['ai'] = {'provider': 'chatgpt'}
        dialog = SettingsDialog(settings, ai_store=NoApiKeyStore(), chatgpt_store=ChatGPTMemoryStore())
        self.addCleanup(dialog.close)
        dialog.navigation.setCurrentRow(dialog.navigation.count() - 1)
        dialog.show()
        self.app.processEvents()
        panel = dialog.ai_panel
        with patch('miner_scanner.ai.chatgpt_auth.sign_in', side_effect=AnalysisError('auth_dependency_missing')) as authenticate:
            panel.sign_in_button.click()
            self.wait(lambda: not panel.active)
            authenticate.assert_called_once()
        self.assertIn('компонент', panel.auth_status.text())
        self.assertTrue(panel.auth_status.isVisible())
        viewport = dialog.pages.currentWidget().viewport()
        point = panel.auth_status.mapTo(viewport, panel.auth_status.rect().center())
        self.assertTrue(viewport.rect().contains(point), 'Authentication feedback must be in view after clicking Continue')
        self.assertFalse(panel.sign_in_button.autoDefault())
        self.assertFalse(panel.manage_usage_button.autoDefault())

    def test_authentication_does_not_require_hidden_yandex_fields_or_selected_model(self):
        panel = self.settings_panel()
        panel.folder.setText('invalid yandex folder')
        panel.chatgpt_model.addItem('invalid model draft', 'invalid model draft')
        panel.chatgpt_model.setCurrentIndex(0)
        with patch('miner_scanner.ai.chatgpt_auth.sign_in') as authenticate, patch.object(panel, '_show_plan_welcome'):
            panel.sign_in_button.click()
            self.wait(lambda: not panel.active)
            authenticate.assert_called_once()
        with patch('miner_scanner.ai.chatgpt_auth.sign_out', return_value=True) as sign_out:
            panel.sign_out_button.click()
            self.wait(lambda: not panel.active)
            sign_out.assert_called_once()

    def test_browser_failure_is_retryable_for_only_the_current_attempt(self):
        panel = self.settings_panel()
        release = Event()
        self.addCleanup(release.set)
        authorization = 'https://auth.openai.com/oauth/authorize?state=fixture&id_token_hint=PRIVATE-IDENTITY-HINT'

        def authenticate(store, *, open_browser, cancel, timeout_seconds, new_account, on_phase=None):
            on_phase('preparing')
            open_browser(authorization)
            on_phase('waiting_browser')
            release.wait(3)
            on_phase('validating')

        with (patch('miner_scanner.ai.chatgpt_auth.sign_in', side_effect=authenticate),
              patch.object(panel, '_show_plan_welcome'),
              patch.object(self.settings.QDesktopServices, 'openUrl', side_effect=[False, True]) as browser):
            panel.sign_in_button.click()
            self.wait(lambda: panel.open_browser_button.isEnabled())
            self.assertTrue(panel.active)
            self.assertIn('Браузер', panel.auth_status.text())
            self.assertFalse(panel._worker.cancel.is_set())
            panel.open_browser_button.click()
            self.assertEqual(browser.call_count, 2)
            self.assertEqual(browser.call_args_list[0].args[0].toString(), browser.call_args_list[1].args[0].toString())
            for label in (panel.status, panel.auth_status, panel.account_status):
                self.assertNotIn('PRIVATE-IDENTITY-HINT', label.text())
                self.assertNotIn('https://', label.text())
            release.set()
            self.wait(lambda: not panel.active)
        self.assertIsNone(panel._authorization_url)
        self.assertTrue(panel.open_browser_button.isHidden())

    def test_cancel_immediately_invalidates_browser_retry_and_late_browser_signal(self):
        panel = self.settings_panel()

        def authenticate(store, *, open_browser, cancel, timeout_seconds, new_account, on_phase=None):
            open_browser('https://auth.openai.com/oauth/authorize?state=fixture')
            cancel.wait(3)

        with (patch('miner_scanner.ai.chatgpt_auth.sign_in', side_effect=authenticate),
              patch.object(self.settings.QDesktopServices, 'openUrl', return_value=True) as browser):
            panel.sign_in_button.click()
            self.wait(lambda: panel.open_browser_button.isEnabled())
            worker = panel._worker
            panel.request_stop()
            self.assertIsNone(panel._authorization_url)
            self.assertTrue(panel.open_browser_button.isHidden())
            panel._open_browser(worker, 'https://auth.openai.com/oauth/authorize?state=old')
            panel.open_browser_button.click()
            self.assertEqual(browser.call_count, 1)
            self.wait(lambda: not panel.active)

    def test_queued_browser_and_phase_events_from_an_old_attempt_are_ignored(self):
        panel = self.settings_panel()
        old = SimpleNamespace(cancel=Event(), kind='sign_in')
        current = SimpleNamespace(cancel=Event(), kind='sign_in')
        try:
            panel._worker = current
            before = panel.auth_status.text()
            with patch.object(self.settings.QDesktopServices, 'openUrl') as browser:
                panel._open_browser(old, 'https://auth.openai.com/oauth/authorize?state=old')
                panel._show_auth_phase(old, 'validating')
                browser.assert_not_called()
            self.assertIsNone(panel._authorization_url)
            self.assertEqual(panel.auth_status.text(), before)
        finally:
            panel._worker = None

    def test_analysis_rejection_shows_public_http_metadata_without_private_response_text(self):
        from PyQt6.QtCore import Qt
        from miner_scanner.ai import AnalysisError
        panel = self.analysis_panel()
        failure = AnalysisError('invalid_request', details=dict(http_status=400,
            provider_code='invalid_request_error', provider_param='input',
            request_id='req_fixture_123', response_shape='error',
            message='RAW-PRIVATE-RESPONSE', access_token='PRIVATE-ACCESS-TOKEN',
            unknown={'password': 'PRIVATE-PASSWORD'}))
        with patch.object(self.analysis, 'analyze', side_effect=failure) as request:
            panel.consent.setChecked(True)
            panel.analyze_button.click()
            self.wait(lambda: not panel.active)
            self.assertEqual(request.call_count, 1)
            panel.start_analysis()
            self.assertEqual(request.call_count, 1, 'An error must not silently retry a paid request')
        text = panel.status.text()
        for public in ('HTTP 400', 'invalid_request_error', 'input', 'req_fixture_123'):
            self.assertIn(public, text)
        for private in ('RAW-PRIVATE-RESPONSE', 'PRIVATE-ACCESS-TOKEN', 'PRIVATE-PASSWORD'):
            self.assertNotIn(private, text)
        self.assertNotIn('Войдите заново', text)
        self.assertIs(panel.status.textFormat(), Qt.TextFormat.PlainText)
        self.assertIsNone(panel.report)
        self.assertEqual(panel.result.toPlainText(), '')
        self.assertFalse(panel.consent.isChecked())

    def test_connection_rejection_metadata_reaches_the_visible_account_status(self):
        from PyQt6.QtCore import Qt
        from miner_scanner.ai import AnalysisError
        panel = self.settings_panel()
        panel.chatgpt_model.addItem('account-model', 'account-model')
        panel.chatgpt_model.setCurrentIndex(0)
        failure = AnalysisError('invalid_request', details=dict(http_status=400,
            provider_code='unsupported_parameter', provider_param='text.format',
            request_id='req_connection_fixture', response_shape='error',
            detail='PRIVATE-RESPONSE-BODY', refresh_token='PRIVATE-REFRESH-TOKEN'))
        with patch('miner_scanner.ai.openai.OpenAIProvider') as provider:
            provider.return_value.check_connection.side_effect = failure
            panel.check_button.click()
            self.wait(lambda: not panel.active)
            provider.return_value.check_connection.assert_called_once()
        for public in ('HTTP 400', 'unsupported_parameter', 'text.format', 'req_connection_fixture'):
            self.assertIn(public, panel.auth_status.text())
        self.assertEqual(panel.auth_status.text(), panel.status.text())
        self.assertNotIn('PRIVATE-RESPONSE-BODY', panel.auth_status.text())
        self.assertNotIn('PRIVATE-REFRESH-TOKEN', panel.auth_status.text())
        self.assertIs(panel.auth_status.textFormat(), Qt.TextFormat.PlainText)

    def test_yandex_connection_errors_keep_the_same_sanitized_metadata_contract(self):
        from miner_scanner.ai import AnalysisError
        from tests.test_ai_dialog import MemoryStore
        panel = self.settings.AISettingsPanel({'folder_id': 'yandex-fixture'},
            store=MemoryStore(), chatgpt_store=ChatGPTMemoryStore())
        self.addCleanup(self.clean, panel)
        failure = AnalysisError('forbidden', details=dict(http_status=403, provider_code='permission_denied',
            provider_param='model', request_id='req_yandex_fixture', message='PRIVATE-MESSAGE'))
        with patch.object(self.settings.YandexProvider, 'check_connection', side_effect=failure):
            panel.check_button.click()
            self.wait(lambda: not panel.active)
        self.assertIn('HTTP 403', panel.status.text())
        self.assertIn('permission_denied', panel.status.text())
        self.assertNotIn('PRIVATE-MESSAGE', panel.status.text())

    def test_error_formatter_revalidates_a_supplied_mapping_and_ignores_unknown_fields(self):
        text = self.analysis.error_text('invalid_request', 'en', 'chatgpt', details=dict(
            http_status=400, provider_param='input[0].content', provider_code='invalid_request',
            request_id='Bearer PRIVATE-TOKEN', message='PRIVATE-MESSAGE', unknown='PRIVATE-UNKNOWN'))
        self.assertIn('HTTP 400', text)
        self.assertIn('Parameter: input[0].content', text)
        self.assertNotIn('PRIVATE', text)

    def test_rejected_analysis_identifies_the_validation_stage_and_keeps_result_empty(self):
        from miner_scanner.ai import AnalysisError
        cases = (
            ('stream', 'stream_incomplete', None, 'Ответ модели не завершён', 'поток ответа'),
            ('output', 'output_refusal', None, 'Модель отказалась', 'формат заключения'),
            ('output', 'output_json', None, 'не является корректным JSON', 'формат заключения'),
            ('output', 'output_fields', None, 'Набор полей', 'формат заключения'),
            ('evidence', 'log_reference_missing', 'evidence[0].reference',
             'строки лога, которые не передавались', 'проверка свидетельств'),
            ('evidence', 'snapshot_reference_missing', 'evidence[1].reference',
             'показания устройства, которые не передавались', 'проверка свидетельств'),
        )
        for stage, reason, field, description, stage_label in cases:
            with self.subTest(reason=reason):
                panel = self.analysis_panel()
                failure = AnalysisError('invalid_response', details=dict(
                    validation_stage=stage, validation_reason=reason, validation_field=field,
                    http_status=200, request_id='req_validation_fixture',
                    model_response='PRIVATE-RESPONSE', message='PRIVATE-MESSAGE'))
                with patch.object(self.analysis, 'analyze', side_effect=failure) as request:
                    panel.consent.setChecked(True)
                    panel.analyze_button.click()
                    self.wait(lambda: not panel.active)
                    panel.start_analysis()
                    self.assertEqual(request.call_count, 1, 'A rejected output needs fresh user consent')
                text = panel.status.text()
                self.assertIn(description, text)
                self.assertIn('Этап: ' + stage_label, text)
                self.assertIn('Заключение не принято', text)
                self.assertIn('HTTP 200', text)
                self.assertIn('req_validation_fixture', text)
                if field:
                    self.assertIn('Поле: ' + field, text)
                self.assertNotIn('PRIVATE', text)
                self.assertNotIn('Войдите', text)
                self.assertIsNone(panel.report)
                self.assertEqual(panel.result.toPlainText(), '')
                self.assertFalse(panel.copy_button.isEnabled())
                self.assertFalse(panel.save_button.isEnabled())
                self.assertFalse(panel.consent.isChecked())

    def test_validation_messages_are_localized_and_unknown_details_remain_generic(self):
        english = self.analysis.error_text('invalid_response', 'en', 'chatgpt', details=dict(
            validation_stage='evidence', validation_reason='log_reference_missing',
            validation_field='evidence[2].reference'))
        self.assertIn('log lines that were not sent', english)
        self.assertIn('Stage: evidence validation', english)
        self.assertIn('Field: evidence[2].reference', english)
        self.assertIn('No assessment was accepted', english)
        for language in ('ru', 'en'):
            with self.subTest(language=language):
                generic = self.analysis.error_text('invalid_response', language, 'chatgpt')
                unknown = self.analysis.error_text('invalid_response', language, 'chatgpt', details=dict(
                    validation_stage='PRIVATE-STAGE', validation_reason='PRIVATE-REASON',
                    validation_field='snapshot.private_password', model_response='PRIVATE-RESPONSE'))
                self.assertEqual(unknown, generic)
                self.assertNotIn('PRIVATE', unknown)

    def test_connection_stream_failure_is_visible_next_to_chatgpt_sign_in_controls(self):
        from miner_scanner.ai import AnalysisError
        panel = self.settings_panel()
        panel.chatgpt_model.addItem('account-model', 'account-model')
        panel.chatgpt_model.setCurrentIndex(0)
        failure = AnalysisError('invalid_response', details=dict(
            validation_stage='stream', validation_reason='stream_incomplete', http_status=200))
        with patch('miner_scanner.ai.openai.OpenAIProvider') as provider:
            provider.return_value.check_connection.side_effect = failure
            panel.check_button.click()
            self.wait(lambda: not panel.active)
            provider.return_value.check_connection.assert_called_once()
        self.assertIn('Ответ модели не завершён', panel.auth_status.text())
        self.assertIn('Этап: поток ответа', panel.auth_status.text())
        self.assertEqual(panel.status.text(), panel.auth_status.text())
