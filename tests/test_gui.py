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
