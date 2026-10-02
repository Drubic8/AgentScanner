"""Offscreen integration checks; no device or update-server access."""
import importlib.util
import os
import socket
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@unittest.skipUnless(importlib.util.find_spec("PyQt6") and importlib.util.find_spec("pandas") and importlib.util.find_spec("fpdf"), "Desktop dependencies not installed")
class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        original_connect = socket.socket.connect
        import gemini_gui
        cls.gui = gemini_gui
        cls.original_connect = staticmethod(original_connect)
        cls.application = gemini_gui.QApplication.instance() or gemini_gui.QApplication([])

    def test_import_does_not_patch_global_sockets(self):
        self.assertIs(socket.socket.connect, self.original_connect)

    def test_mining_menus_use_wakeup_and_separate_supported_power_modes(self):
        from PyQt6.QtCore import QPoint
        from miner_scanner.service import ScannerService
        from miner_scanner.repository import DeviceRepository
        from tests.fakes import FakeFactory
        repository = DeviceRepository(':memory:')
        self.addCleanup(repository.close)
        row = ScannerService(repository=repository, transport_factory=FakeFactory()).poll('192.0.2.1').to_legacy()
        row['Capabilities'] = {'hem': 'supported', 'normal_power': 'supported', 'low': 'unsupported'}
        window = self.gui.GeminiApp(settings={}, ranges=[])
        try:
            window.on_result([row])
            menus = [window.btn_remote.menu()]
            with patch.object(self.gui.QMenu, 'exec', lambda menu, *_: menus.append(menu)):
                window.show_context_menu(QPoint(0, 0))
            self.assertEqual(len(menus), 2)
            for menu in menus:
                menu.aboutToShow.emit()
                power_menu = next(item.menu() for item in menu.actions() if item.menu())
                self.assertFalse(power_menu.isEnabled())
                window.table.item(0, 0).setCheckState(self.gui.Qt.CheckState.Checked)
                menu.aboutToShow.emit()
                self.assertTrue(power_menu.isEnabled())
                low, normal, hem = power_menu.actions()
                self.assertFalse(low.isEnabled())
                self.assertTrue(normal.isEnabled())
                self.assertTrue(hem.isEnabled())
                with patch.object(window, 'run_action') as run:
                    next(item for item in menu.actions() if item.text().startswith('Пробуждение')).trigger()
                    run.assert_called_once_with('wakeup')
                window.table.item(0, 0).setCheckState(self.gui.Qt.CheckState.Unchecked)
        finally:
            window.close()

    def test_wakeup_confirmation_explains_avalon_reboot(self):
        window = self.gui.GeminiApp(settings={}, ranges=[])
        try:
            window.on_result([{'IP': '192.0.2.134', 'Model': 'Avalon 1346-110', 'ProfileId': 'canaan.avalon'}])
            window.table.item(0, 0).setCheckState(self.gui.Qt.CheckState.Checked)
            with patch.object(self.gui.QMessageBox, 'question', return_value=self.gui.QMessageBox.StandardButton.No) as question, \
                 patch.object(self.gui, 'ActionWorker') as worker:
                window.run_action('wakeup')
            self.assertIn('Avalon', question.call_args.args[2])
            self.assertIn('перезагрузку', question.call_args.args[2])
            worker.assert_not_called()
        finally:
            window.close()

    def test_error_tooltip_preserves_lines_and_escapes_device_html(self):
        from miner_scanner.parsers.whatsminer import parse_whatsminer_data
        from PyQt6.QtGui import QTextDocument
        row = parse_whatsminer_data('192.0.2.1', {
            'code': 0, 'msg': {'miner': {'type': 'M61', 'working': True},
                              'error-code': [{'2310': 123, 'reason': '<b>Device & fault</b>'}]},
        }, None, None)
        window = self.gui.GeminiApp(settings={}, ranges=[])
        try:
            window.on_result([row])
            item = window.table.item(0, 4)
            self.assertEqual(item.text(), '2310')
            self.assertNotIn('<b>Device', item.toolTip())
            document = QTextDocument()
            document.setHtml(item.toolTip())
            text = document.toPlainText()
            self.assertIn('<b>Device & fault</b>', text)
            self.assertIn('низкий хешрейт', text)
            self.assertIn('\n', text)
            self.assertIn('03.06.2026, стр. 3', text)
        finally:
            window.close()

    def test_access_editor_saves_edits_on_selection_and_validates_scope(self):
        from unittest.mock import Mock
        from desktop_ui.access_dialog import AccessProfilesDialog
        from miner_scanner.access import AccessProfiles, standard_profiles
        from PyQt6.QtWidgets import QLineEdit
        store = Mock()
        dialog = AccessProfilesDialog(AccessProfiles(standard_profiles()), store, target_ips=['192.0.2.1'])
        try:
            self.assertEqual(dialog.password.echoMode(), QLineEdit.EchoMode.Password)
            dialog.name.setText('Site Antminer')
            dialog.list.setCurrentRow(1)
            self.assertEqual(dialog.profiles[0].name, 'Site Antminer')
            self.assertFalse(dialog.username.isEnabled())
            dialog.add_profile()
            self.assertEqual(dialog.targets.text(), '192.0.2.1')
            dialog.targets.setText('not-an-ip')
            dialog.save_profiles()
            store.save.assert_not_called()
            self.assertTrue(dialog.error.text())
            dialog.targets.setText('192.0.2.0/24')
            dialog.password.setText('test-profile-password')
            dialog.save_profiles()
            store.save.assert_called_once()
            self.assertEqual(dialog.result_profiles.profiles[-1].password, 'test-profile-password')
            self.assertNotIn('test-profile-password', '\n'.join(dialog.list.item(i).text() for i in range(dialog.list.count())))
        finally:
            dialog.close()

    def test_gui_initialization_loads_automatic_profiles(self):
        from unittest.mock import Mock
        from miner_scanner.access import AccessProfiles, standard_profiles
        profiles = AccessProfiles(standard_profiles())
        service = Mock()
        with patch.object(self.gui.AccessStore, 'load', return_value=profiles), patch.object(self.gui, 'default_service', return_value=service):
            window = self.gui.GeminiApp(settings={}, ranges=[])
            try:
                service.set_access_profiles.assert_called_once_with(profiles)
            finally:
                window.close()

    def test_rows_keep_identity_through_sort_and_unknown_metrics(self):
        from miner_scanner.service import ScannerService
        from miner_scanner.repository import DeviceRepository
        from tests.fakes import FakeFactory
        repository = DeviceRepository(":memory:")
        service = ScannerService(repository=repository, transport_factory=FakeFactory())
        self.addCleanup(repository.close)
        rows = [service.poll("192.0.2.2").to_legacy(), service.poll("192.0.2.1").to_legacy()]
        rows[1]["Real"], rows[1]["RawHash"] = "—", None
        with patch.object(self.gui.GeminiApp, "check_for_updates"), patch.object(self.gui.GeminiApp, "load_config", return_value=[]), patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected network request")):
            window = self.gui.GeminiApp()
            try:
                window.on_result(rows)
                window.table.setSortingEnabled(True)
                window.table.sortItems(0, self.gui.Qt.SortOrder.AscendingOrder)
                item = window.table.item(0, 0)
                saved = item.data(self.gui.Qt.ItemDataRole.UserRole + 1)
                self.assertEqual(saved["IP"], "192.0.2.1")
                self.assertEqual(saved["DeviceId"], rows[1]["DeviceId"])
                self.assertEqual(window.table.rowCount(), 2)
            finally:
                window.close()

    def test_worker_streams_completed_rows_and_progress(self):
        from miner_scanner.service import ScannerService
        from miner_scanner.repository import DeviceRepository
        from tests.fakes import FakeFactory
        repository = DeviceRepository(":memory:")
        service = ScannerService(repository=repository, transport_factory=FakeFactory())
        self.addCleanup(repository.close)
        worker = self.gui.ScanWorker(["192.0.2.1-2"], ["Bitmain"])
        rows, progress = [], []
        worker.result_signal.connect(rows.extend)
        worker.progress_signal.connect(lambda current, total: progress.append((current, total)))
        with patch("miner_scanner.core.default_service", return_value=service):
            worker.run()
        self.assertEqual(len(rows), 2)
        self.assertEqual(progress[-1], (2, 2))

    def test_failed_rescan_does_not_show_cached_device(self):
        from miner_scanner.service import ScannerService
        from miner_scanner.repository import DeviceRepository
        from tests.fakes import FakeFactory, stock
        factory = FakeFactory()
        repository = DeviceRepository(":memory:")
        self.addCleanup(repository.close)
        service = ScannerService(repository=repository, transport_factory=factory)
        service.poll("192.0.2.1")
        factory.data.clear()
        stale = service.poll("192.0.2.1", force_identify=True).to_legacy()
        window = self.gui.GeminiApp(settings={}, ranges=[])
        try:
            with patch.object(window, "add_log") as log:
                window.on_result([stale])
            self.assertEqual(window.table.rowCount(), 0)
            self.assertEqual(window.scan_data, [])
            log.assert_called_once()
            self.assertIn("192.0.2.1", log.call_args.args[0])
            factory.data.update(stock())
            window.on_result([service.poll("192.0.2.1").to_legacy()])
            self.assertEqual(window.table.rowCount(), 1)
        finally:
            window.close()

    def test_dashboard_details_scroll_and_survive_refresh(self):
        window = self.gui.GeminiApp(settings={}, ranges=[])
        try:
            window.show()
            models = {f"Antminer Model {i}": {"val": "12"} for i in range(30)}
            window.refresh_dashboard({}, models, {})
            self.application.processEvents()
            detail = window.layout_models.itemAt(2).widget()
            bar = detail.verticalScrollBar()
            self.assertGreater(bar.maximum(), 0)
            bar.setValue(bar.maximum())
            position = bar.value()
            window.refresh_dashboard({}, models, {})
            self.assertIs(window.layout_models.itemAt(2).widget(), detail)
            self.assertEqual(bar.value(), position)
            self.assertIn("Antminer Model 29", detail.toPlainText())
        finally:
            window.close()
