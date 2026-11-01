"""Offline Elphapex current-log contract, auth boundaries and card availability."""
import importlib.util
import os
from pathlib import Path
from threading import Event
import unittest
from unittest.mock import patch

import requests

from miner_scanner.access import AccessProfile, AccessProfiles
from miner_scanner.device_logs import MAX_LOG_BYTES, read_device_log, supported_log_kinds
from miner_scanner.runtime import AuthenticationError, DeadlineExceeded, ProtocolError
from tests.test_device_logs import ScriptedFactory


DEVICE = {'IP': '192.0.2.96', 'ProfileId': 'elphapex.luci', 'Model': 'Elphapex DG1'}
ROOT_LOG = '/cgi-bin/hlog.cgi'
LUCI_LOG = '/cgi-bin/luci/hlog.cgi'
NO_ACCESS = AccessProfiles([])


class ElphapexDeviceLogTests(unittest.TestCase):
    def read(self, responses=(), *, row=DEVICE, kind='miner', profiles=NO_ACCESS, **kwargs):
        factory = ScriptedFactory(*responses)
        result = read_device_log(row, kind, access_profiles=profiles,
                                 transport_factory=factory, **kwargs)
        return result, factory

    def test_exact_profile_enables_current_miner_log_only(self):
        self.assertEqual(supported_log_kinds(DEVICE), ('miner',))
        nested = {'IP': DEVICE['IP'], 'Identity': {'profile_id': 'elphapex.luci'}}
        self.assertEqual(supported_log_kinds(nested), ('miner',))
        for row in ({'IP': DEVICE['IP'], 'Model': 'Elphapex DG1+'},
                    dict(DEVICE, ProfileId='elphapex.unknown'),
                    dict(DEVICE, Identity={'profile_id': 'bitmain.stock'})):
            result, factory = self.read(row=row)
            self.assertEqual(result.status, 'unsupported')
            self.assertEqual(factory.calls, [])
        for kind in ('system', 'history', '../reboot.cgi'):
            result, factory = self.read(kind=kind)
            self.assertEqual(result.status, 'unsupported')
            self.assertEqual(factory.calls, [])

    def test_current_log_is_anonymous_get_with_bounded_shared_budget(self):
        profiles = AccessProfiles([AccessProfile('Device', 'elphapex', 'fixture', 'private', auth='basic')])
        with patch.object(profiles, 'candidates', side_effect=AssertionError('Public logs need no credentials')) as candidates:
            result, factory = self.read([(200, b'board 0 initialized\r\nfan 1 running\n')], profiles=profiles)
            candidates.assert_not_called()
        self.assertEqual(result.status, 'succeeded')
        self.assertEqual(result.text, 'board 0 initialized\nfan 1 running\n')
        self.assertEqual(result.source, ROOT_LOG)
        self.assertFalse(result.truncated)
        self.assertEqual(factory.calls, [(DEVICE['IP'], ROOT_LOG, 'GET', None,
                                         {'response_limit': MAX_LOG_BYTES})])
        self.assertEqual(factory.instances[0].operation.options.device_timeout, 20.0)
        self.assertTrue(factory.instances[0].closed)

    def test_luci_alias_is_used_only_when_root_route_is_missing(self):
        for status in (404, 405, 501):
            with self.subTest(status=status):
                result, factory = self.read([(status, b''), (200, b'current miner log')])
                self.assertEqual((result.status, result.source), ('succeeded', LUCI_LOG))
                self.assertEqual([call[1:4] for call in factory.calls],
                                 [(ROOT_LOG, 'GET', None), (LUCI_LOG, 'GET', None)])
                self.assertEqual(len(factory.instances), 1)
                self.assertTrue(factory.instances[0].closed)
        result, factory = self.read([(404, b''), (404, b'')])
        self.assertEqual((result.status, result.text, result.source), ('unsupported', '', LUCI_LOG))
        self.assertEqual(len(factory.calls), 2)

    def test_server_redirect_or_invalid_body_does_not_trigger_route_fanout(self):
        for response, expected in (((500, b'failure'), 'unavailable'),
                                   ((429, b'wait'), 'unavailable'),
                                   ((302, b''), 'unavailable'),
                                   ((200, b'<html><form>login</form></html>'), 'invalid_response'),
                                   ((200, b'{"error":"login required"}'), 'invalid_response')):
            with self.subTest(expected=expected, status=response[0]):
                result, factory = self.read([response])
                self.assertEqual((result.status, result.text), (expected, ''))
                self.assertEqual(len(factory.calls), 1)

    def test_only_explicit_elphapex_profiles_are_used_after_auth_rejection(self):
        profiles = AccessProfiles([
            AccessProfile('General', 'elphapex', 'fixture-general', 'general', auth='digest'),
            AccessProfile('Specific', 'elphapex', 'fixture-specific', 'specific', auth='basic',
                          targets=DEVICE['IP']),
            AccessProfile('Antminer', 'antminer', 'fixture-other', 'wrong-family'),
            AccessProfile('Unrelated', 'elphapex', 'fixture-unrelated', 'wrong-ip', targets='192.0.2.8'),
        ])
        result, factory = self.read([AuthenticationError('private'), (401, b''), (200, b'log')],
                                    profiles=profiles)
        self.assertEqual(result.status, 'succeeded')
        credentials = [call[3] for call in factory.calls]
        self.assertIsNone(credentials[0])
        self.assertEqual([(cred.username, cred.auth) for cred in credentials[1:]],
                         [('fixture-specific', 'basic'), ('fixture-general', 'digest')])
        self.assertTrue(all(call[1:3] == (ROOT_LOG, 'GET') for call in factory.calls))
        self.assertTrue(all(item.operation is factory.instances[0].operation for item in factory.instances))
        self.assertTrue(all(item.closed for item in factory.instances))

    def test_auth_profiles_can_read_missing_root_via_luci_without_post(self):
        profiles = AccessProfiles([AccessProfile('Device', 'elphapex', 'fixture', 'private', auth='basic')])
        result, factory = self.read([(403, b''), (404, b''), (200, b'alias log')], profiles=profiles)
        self.assertEqual((result.status, result.source), ('succeeded', LUCI_LOG))
        self.assertEqual([call[1:3] for call in factory.calls],
                         [(ROOT_LOG, 'GET'), (ROOT_LOG, 'GET'), (LUCI_LOG, 'GET')])
        self.assertTrue(all(set(call[4]) == {'response_limit'} for call in factory.calls))

    def test_empty_disabled_and_wrong_family_never_restore_default_credentials(self):
        cases = (NO_ACCESS,
                 AccessProfiles([AccessProfile('Disabled', 'elphapex', 'fixture', 'private', enabled=False)]),
                 AccessProfiles([AccessProfile('Antminer', 'antminer', 'fixture', 'private')]))
        for profiles in cases:
            result, factory = self.read([(401, b'')], profiles=profiles)
            self.assertEqual(result.status, 'auth_required')
            self.assertEqual(len(factory.calls), 1)
            self.assertIsNone(factory.calls[0][3])

    def test_http_failure_after_auth_does_not_cycle_remaining_passwords(self):
        profiles = AccessProfiles([AccessProfile('One', 'elphapex', 'one', 'one'),
                                   AccessProfile('Two', 'elphapex', 'two', 'two')])
        for status in (500, 429):
            result, factory = self.read([(401, b''), (status, b'error')], profiles=profiles)
            self.assertEqual(result.status, 'unavailable')
            self.assertEqual(len(factory.calls), 2)

    def test_binary_and_size_errors_are_not_partial_logs(self):
        for response, expected in (((200, b'bad\x00binary'), 'invalid_response'),
                ((200, b'x' * (MAX_LOG_BYTES + 1)), 'too_large'),
                (ProtocolError('HTTP response too large'), 'too_large')):
            result, factory = self.read([response])
            self.assertEqual((result.status, result.text), (expected, ''))
            self.assertEqual(len(factory.calls), 1)
            self.assertTrue(factory.instances[0].closed)

    def test_empty_current_log_is_distinct_from_missing_route(self):
        result, factory = self.read([(200, b'')])
        self.assertEqual((result.status, result.text, result.source), ('succeeded', '', ROOT_LOG))
        self.assertEqual(len(factory.calls), 1)

    def test_cancellation_identity_and_deadline_stop_without_extra_requests(self):
        cancel = Event()
        cancel.set()
        result, factory = self.read(cancel=cancel)
        self.assertEqual(result.status, 'cancelled')
        self.assertEqual(factory.instances, [])
        result, factory = self.read(row=dict(DEVICE, Identity={'ip': '192.0.2.10'}))
        self.assertEqual(result.status, 'unsupported')
        self.assertEqual(factory.instances, [])
        for error, expected in ((DeadlineExceeded('private'), 'timeout'),
                                (requests.Timeout('private'), 'timeout'),
                                (requests.ConnectionError('private'), 'unavailable')):
            result, factory = self.read([error])
            self.assertEqual(result.status, expected)
            self.assertEqual(len(factory.calls), 1)
            self.assertTrue(factory.instances[0].closed)
            self.assertNotIn('private', repr(result))


@unittest.skipUnless(importlib.util.find_spec('PyQt6'), 'Desktop dependencies not installed')
class ElphapexLogDialogTests(unittest.TestCase):
    def test_card_offers_only_current_miner_log_without_fetching_on_open(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtGui import QFontDatabase
        from desktop_ui.i18n import set_language
        import desktop_ui.device_dialog as module
        app = QApplication.instance() or QApplication([])
        fonts = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
        QFontDatabase.addApplicationFont(str(fonts / 'segoeui.ttf'))
        set_language('ru')
        with patch.object(module, 'read_device_log') as read:
            dialog = module.DeviceDialog(DEVICE, access_profiles=NO_ACCESS, app_version='test')
            try:
                app.processEvents()
                read.assert_not_called()
                self.assertTrue(dialog.fetch_button.isEnabled())
                self.assertEqual(dialog.kind_box.count(), 1)
                self.assertEqual(dialog.kind_box.currentData(), 'miner')
                self.assertTrue(dialog.open_button.isEnabled())
                self.assertFalse(dialog.active)
            finally:
                dialog.close()
                app.processEvents()


if __name__ == '__main__':
    unittest.main()
