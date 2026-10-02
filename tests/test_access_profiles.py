import os
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from desktop_ui.access_store import AccessStore, MAGIC
from miner_scanner.access import AccessProfile, AccessProfiles, standard_profiles
from miner_scanner.commands import vnish
from miner_scanner.models import Credentials
from miner_scanner.repository import DeviceRepository
from miner_scanner.runtime import AuthenticationError
from miner_scanner.service import ScannerService
from tests.fakes import FakeFactory, FakeTransport


class AuthTransport(FakeTransport):
    def __init__(self, factory, ip, operation, credentials=None):
        super().__init__(factory, ip, operation, credentials)
        self.credentials = credentials
        factory.tried.append(credentials)

    def http_json(self, path, method='GET', **kwargs):
        if path in ('/cgi-bin/get_system_info.cgi', '/cgi-bin/get_miner_conf.cgi'):
            if self.credentials is None or self.credentials.password != self.factory.password:
                raise AuthenticationError('Required')
        return super().http_json(path, method, **kwargs)


class AuthFactory(FakeFactory):
    def __init__(self, password='root'):
        super().__init__({'system': {'minertype': 'Antminer L7'}, 'config': {'bitmain-work-mode': 1}})
        self.password = password
        self.tried = []

    def __call__(self, ip, operation, credentials=None):
        return AuthTransport(self, ip, operation, credentials)


class AccessProfileTests(unittest.TestCase):
    def test_defaults_and_ip_scope_priority(self):
        custom = AccessProfile('Site', 'antminer', 'root', 'site-secret', targets='10.33.6.0/24')
        profiles = AccessProfiles([*standard_profiles(), custom])
        self.assertEqual(profiles.candidates('10.33.6.13')[0].password, 'site-secret')
        self.assertEqual(profiles.candidates('192.0.2.1')[0].password, 'root')
        self.assertEqual([c.password for c in profiles.candidates('192.0.2.1', 'vnish')], ['admin', 'root'])
        self.assertEqual(profiles.candidates('192.0.2.1', 'whatsminer'), [Credentials('super', 'super')])
        scoped = AccessProfile('WhatsMiner site', 'whatsminer', 'user1', 'site-secret', targets='10.33.6.0/24')
        wm = AccessProfiles([*standard_profiles(), scoped])
        self.assertEqual(wm.candidates('10.33.6.13', 'whatsminer')[0], scoped.credentials)
        self.assertEqual(wm.candidates('10.10.91.247', 'whatsminer'), [Credentials('super', 'super')])
        self.assertNotIn('site-secret', repr(custom))
        for targets in ('example.com', '::1', '10.0.0.1,'):
            with self.assertRaises(ValueError):
                AccessProfile('Bad', 'antminer', 'root', '', targets=targets)

    def service(self, factory):
        repository = DeviceRepository(':memory:')
        self.addCleanup(repository.close)
        return ScannerService(repository=repository, transport_factory=factory)

    def test_standard_password_works_without_session_configuration(self):
        factory = AuthFactory()
        service = self.service(factory)
        service.set_access_profiles(AccessProfiles(standard_profiles()))
        self.assertEqual(service.poll('192.0.2.1').display['Status'], 'Sleep')
        self.assertEqual(len(factory.tried), 1)
        self.assertFalse(factory.writes)

    def test_read_failover_remembers_success_and_explicit_override_wins(self):
        factory = AuthFactory('correct')
        service = self.service(factory)
        service.set_access_profiles(AccessProfiles([
            AccessProfile('First', 'antminer', 'root', 'wrong'),
            AccessProfile('Second', 'antminer', 'root', 'correct')]))
        self.assertEqual(service.poll('192.0.2.1').display['Status'], 'Sleep')
        self.assertEqual([c.password for c in factory.tried], ['wrong', 'correct'])
        factory.tried.clear()
        self.assertEqual(service.poll('192.0.2.1').display['Status'], 'Sleep')
        self.assertEqual([c.password for c in factory.tried], ['correct'])
        service.set_credentials('192.0.2.1', Credentials('root', 'explicit'))
        factory.tried.clear()
        record = service.poll('192.0.2.1', force_identify=True)
        self.assertEqual(record.display['Error'], 'AUTH REQUIRED')
        self.assertEqual([c.password for c in factory.tried], ['explicit'])
        self.assertFalse(factory.writes)

    def test_disabled_profiles_and_attempt_limit(self):
        profiles = AccessProfiles([AccessProfile(str(i), 'antminer', 'root', str(i)) for i in range(6)])
        self.assertEqual(len(profiles.candidates('192.0.2.1')), 3)
        self.assertFalse(AccessProfiles([AccessProfile('Disabled', 'antminer', 'root', 'root', enabled=False)]).candidates('192.0.2.1'))

    def test_vnish_unlock_fallback_does_not_repeat_control_write(self):
        transport = Mock()
        transport.http_json.side_effect = [AuthenticationError('Wrong'), {'token': 'test-token'},
                                          {'miner': {'miner_status': {'miner_state': 'stopped'}}}]
        transport.http.return_value = (200, b'')
        candidates = AccessProfiles(standard_profiles()).candidates('192.0.2.1', 'vnish')
        authenticated = []
        accepted, verify = vnish(transport, None, 'mining_stop', candidates[0],
                                 credential_candidates=candidates, on_authenticated=authenticated.append)
        self.assertTrue(accepted)
        self.assertTrue(verify())
        self.assertEqual(authenticated, [candidates[1]])
        self.assertEqual(transport.http.call_count, 1)
        self.assertEqual(transport.http_json.call_args_list[0].kwargs['payload'], {'pw': 'admin'})
        self.assertEqual(transport.http_json.call_args_list[1].kwargs['payload'], {'pw': 'root'})

    def test_vnish_does_not_try_other_password_after_rate_limit(self):
        transport = Mock()
        transport.http_json.return_value = None  # HTTP 429 / non-200, not a 401/403
        candidates = AccessProfiles(standard_profiles()).candidates('192.0.2.1', 'vnish')
        with self.assertRaises(AuthenticationError):
            vnish(transport, None, 'reboot', candidates[0], credential_candidates=candidates)
        self.assertEqual(transport.http_json.call_count, 1)
        transport.http.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI integration')
    def test_windows_storage_roundtrip_and_no_plaintext(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'profiles.dat'
            store = AccessStore(path)
            self.assertEqual(len(store.load().profiles), 4)
            profiles = AccessProfiles([AccessProfile('Site', 'antminer', 'root', 'unique-test-secret-928')])
            store.save(profiles)
            self.assertNotIn(b'unique-test-secret-928', path.read_bytes())
            self.assertEqual(AccessStore(path).load().profiles, profiles.profiles)
            store.save(AccessProfiles([]))
            self.assertEqual(store.load().profiles, ())
            path.write_bytes(b'broken')
            with self.assertRaises(ValueError):
                store.load()

    def test_failed_protection_does_not_overwrite_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'profiles.dat'
            path.write_bytes(b'original-encrypted-file')
            store = AccessStore(path, crypt=Mock(side_effect=OSError('Protection failed')))
            with self.assertRaises(OSError):
                store.save(AccessProfiles(standard_profiles()))
            self.assertEqual(path.read_bytes(), b'original-encrypted-file')

    def test_old_empty_whatsminer_template_migrates_without_changing_user_profiles(self):
        from dataclasses import asdict, replace
        template = AccessProfile('WhatsMiner — все устройства', 'whatsminer', 'super', '')
        original = [template, replace(template, password='custom'), replace(template, enabled=False),
                    replace(template, username='user1'), replace(template, targets='192.0.2.1'),
                    replace(template, name='Custom profile')]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'mock-encrypted.dat'
            # Identity crypt is confined to this synthetic storage test.
            store = AccessStore(path, crypt=lambda value, **_: value)
            path.write_bytes(MAGIC + json.dumps({'version': 1, 'profiles': [asdict(p) for p in original]}).encode())
            migrated = store.load()
            self.assertEqual(migrated.profiles[0].password, 'super')
            self.assertEqual(migrated.profiles[1:], tuple(original[1:]))
            store.save(AccessProfiles([template]))  # User explicitly clears it after migration.
            self.assertEqual(store.load().profiles, (template,))
