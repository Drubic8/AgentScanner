"""Locate-state telemetry, stale presentation and native table integration."""
import importlib.util
import os
import unittest
from unittest.mock import Mock, patch

from miner_scanner.identify import read_state
from miner_scanner.repository import DeviceRepository
from miner_scanner.service import ScannerService
from tests.fakes import FakeFactory, stock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


class IdentifyTests(unittest.TestCase):
    def test_missing_and_malformed_values_are_never_off(self):
        transport = Mock()
        for control, field in [('antminer', 'blink'), ('pitbit', 'blink'),
                               ('elphapex', 'blink'), ('vnish', 'find_miner')]:
            for value in [True, False, 'false', 0, None]:
                transport.http_json.return_value = {field: value}
                self.assertIs(read_state(control, transport), value if type(value) is bool else None)
        transport.http_json.side_effect = TimeoutError()
        self.assertIsNone(read_state('vnish', transport))
        transport.operation.errors.append.assert_called_with('identify:unavailable')
        self.assertFalse(transport.http.called)

    def test_each_poll_refreshes_led_even_with_cached_identity(self):
        factory = FakeFactory(stock())
        factory.data['/cgi-bin/get_blink_status.cgi'] = {'blink': False}
        repo = DeviceRepository(':memory:')
        self.addCleanup(repo.close)
        service = ScannerService(repository=repo, transport_factory=factory)
        first = service.poll('192.0.2.1')
        self.assertIs(first.to_legacy()['IdentifyEnabled'], False)
        factory.data['/cgi-bin/get_blink_status.cgi']['blink'] = True
        second = service.poll('192.0.2.1')
        self.assertEqual(second.identity.device_id, first.identity.device_id)
        self.assertIs(second.to_legacy()['IdentifyEnabled'], True)
        self.assertEqual(second.to_legacy()['LED'], 'Включена')
        factory.data['/cgi-bin/get_blink_status.cgi'] = None
        third = service.poll('192.0.2.1')
        self.assertIsNone(third.to_legacy()['IdentifyEnabled'])
        self.assertEqual(third.to_legacy()['LED'], 'Неизвестно')
        self.assertFalse(third.telemetry.stale)
        self.assertFalse(factory.writes)

    def test_saved_or_stale_snapshot_never_shows_current_led_on(self):
        factory = FakeFactory(stock())
        factory.data['/cgi-bin/get_blink_status.cgi'] = {'blink': True}
        repo = DeviceRepository(':memory:')
        self.addCleanup(repo.close)
        service = ScannerService(repository=repo, transport_factory=factory)
        record = service.poll('192.0.2.1')
        saved = repo.get('192.0.2.1')
        self.assertTrue(saved.telemetry.identify_enabled)
        self.assertIsNone(saved.to_legacy()['IdentifyEnabled'])
        self.assertEqual(saved.to_legacy()['LED'], 'Неизвестно')
        record.telemetry.stale = True
        self.assertIsNone(record.to_legacy()['IdentifyEnabled'])

    def test_old_settings_gain_led_once_and_can_then_hide_it(self):
        from desktop_ui.preferences import normalize
        migrated = normalize({'ui_cols': ['IP', 'Model']})
        self.assertEqual(migrated['ui_cols'], ['IP', 'Model', 'LED'])
        migrated['ui_cols'].remove('LED')
        self.assertEqual(normalize(migrated)['ui_cols'], ['IP', 'Model'])


@unittest.skipUnless(importlib.util.find_spec('PyQt6') and importlib.util.find_spec('pandas')
                     and importlib.util.find_spec('fpdf'), 'Desktop dependencies not installed')
class IdentifyUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gemini_gui
        cls.gui = gemini_gui
        cls.app = gemini_gui.QApplication.instance() or gemini_gui.QApplication([])

    def test_indicator_follows_ip_after_sort_refresh_and_stale_read(self):
        from desktop_ui.preferences import COLUMNS, defaults
        from desktop_ui.led_indicator import STATE_ROLE
        from desktop_ui.reports import export_frame
        window = self.gui.GeminiApp(settings=defaults(), ranges=[])
        self.addCleanup(window.close)
        col = list(COLUMNS).index('LED')
        self.assertEqual(window.table.horizontalHeader().visualIndex(col), 1)
        self.assertEqual(window.table.horizontalHeaderItem(col).text(), 'LED')
        self.assertLessEqual(window.table.columnWidth(col), 46)
        rows = [{'IP': '192.0.2.2', 'SortIP': 2, 'IdentifyEnabled': False, 'LED': 'Выключена'},
                {'IP': '192.0.2.1', 'SortIP': 1, 'IdentifyEnabled': True, 'LED': 'Включена'}]
        window.on_result(rows)
        window.table.sortItems(0, self.gui.Qt.SortOrder.AscendingOrder)
        item = window.table.item(0, col)
        self.assertEqual(item.text(), 'Включена')
        self.assertIs(item.data(STATE_ROLE), True)
        window.on_result([dict(rows[1], Stale=True)])
        item = window.table.item(0, col)
        self.assertEqual(item.text(), 'Неизвестно')
        self.assertIsNone(item.data(STATE_ROLE))
        self.assertEqual(export_frame(window.scan_data, ['IP', 'LED'], 'IP').iloc[0]['LED'], 'Неизвестно')
        self.assertEqual(export_frame(rows, ['IP', 'LED'], 'IP').iloc[0]['LED'], 'Включена')
