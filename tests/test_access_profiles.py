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
        self.assertEqual(profiles.candidates('192.0.2.1', 'elphapex'), [Credentials('root', 'root', 'basic')])
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

    def test_other_devices_reuse_http_discovery_profile_without_an_explicit_profile(self):
        profiles = AccessProfiles(standard_profiles())
        self.assertEqual(profiles.candidates('192.0.2.1', 'other'),
                         profiles.candidates('192.0.2.1'))
        self.assertEqual(AccessProfiles([]).candidates('192.0.2.1', 'other'), [])

    def test_other_explicit_profile_and_disabled_profile_override_discovery_fallback(self):
        from dataclasses import replace
        custom = AccessProfile('Jasminer site', 'other', 'admin', 'custom', targets='192.0.2.14')
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                profiles = AccessProfiles([*standard_profiles(), replace(custom, enabled=enabled)])
                self.assertEqual(profiles.candidates('192.0.2.14', 'other'),
                                 [custom.credentials] if enabled else [])
                self.assertEqual(profiles.candidates('192.0.2.15', 'other'),
                                 profiles.candidates('192.0.2.15'))

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
            self.assertEqual(len(store.load().profiles), 5)
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

    @staticmethod
    def former_defaults():
        return (
            AccessProfile('Antminer — стандартный', 'antminer', 'root', 'root'),
            AccessProfile('VNish — admin', 'vnish', '', 'admin'),
            AccessProfile('VNish — root', 'vnish', '', 'root'),
            AccessProfile('WhatsMiner — все устройства', 'whatsminer', 'super', 'super'),
        )

    def test_elphapex_default_is_visible_basic_and_family_scoped(self):
        from dataclasses import replace
        defaults = standard_profiles()
        elphapex = [profile for profile in defaults if profile.family == 'elphapex']
        self.assertEqual(len(elphapex), 1)
        self.assertEqual(elphapex[0].name, 'Elphapex — стандартный')
        self.assertEqual(elphapex[0].credentials, Credentials('root', 'root', 'basic'))
        profiles = AccessProfiles(defaults)
        self.assertEqual(profiles.candidates('192.0.2.17', 'elphapex'), [elphapex[0].credentials])
        self.assertEqual(profiles.candidates('192.0.2.17'), [Credentials('root', 'root', 'digest')])
        self.assertEqual(AccessProfiles([]).candidates('192.0.2.17', 'elphapex'), [])
        disabled = AccessProfiles([replace(profile, enabled=False) if profile.family == 'elphapex' else profile
                                   for profile in defaults])
        self.assertEqual(disabled.candidates('192.0.2.17', 'elphapex'), [])

    def test_new_store_returns_defaults_without_creating_file_or_using_dpapi(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'not-created.dat'
            crypt = Mock(side_effect=AssertionError('No encrypted file exists'))
            loaded = AccessStore(path, crypt=crypt).load()
            self.assertEqual(loaded.profiles, standard_profiles())
            self.assertFalse(path.exists())
            crypt.assert_not_called()

    def test_exact_old_defaults_gain_elphapex_in_memory_only_after_whatsminer_migration(self):
        from dataclasses import asdict, replace
        old = self.former_defaults()
        for revision in (None, 1, 2):
            with self.subTest(revision=revision), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'synthetic-profiles.dat'
                profiles = list(old)
                if revision in (None, 1):
                    profiles[-1] = replace(profiles[-1], password='')
                data = {'version': 1, 'profiles': [asdict(profile) for profile in profiles]}
                if revision is not None:
                    data['defaults_revision'] = revision
                original = MAGIC + json.dumps(data).encode()
                path.write_bytes(original)
                # Identity crypt is confined to this synthetic storage test.
                store = AccessStore(path, crypt=lambda value, **_: value)
                loaded = store.load()
                self.assertEqual(loaded.profiles, standard_profiles())
                self.assertEqual(loaded.profiles[:4], old)
                self.assertEqual(path.read_bytes(), original)

    def test_elphapex_migration_preserves_any_custom_disabled_scoped_or_empty_set(self):
        from dataclasses import asdict, replace
        old = self.former_defaults()
        custom = AccessProfile('Elphapex custom', 'elphapex', 'operator', 'test-only', auth='basic')
        variants = {
            'empty': (),
            'custom_family_only': (custom,),
            'disabled_family_only': (replace(custom, enabled=False),),
            'custom_family': (*old, custom),
            'disabled_family': (*old, replace(custom, enabled=False)),
            'scoped_family': (*old, replace(custom, targets='192.0.2.17')),
            'scoped_disabled_family': (*old, replace(custom, targets='192.0.2.17', enabled=False)),
            'disabled_existing_default': (replace(old[0], enabled=False), *old[1:]),
            'custom_existing_password': (replace(old[0], password='test-only'), *old[1:]),
            'scoped_existing_default': (replace(old[0], targets='192.0.2.0/24'), *old[1:]),
            'missing_default': old[:-1],
            'reordered_defaults': tuple(reversed(old)),
            'extra_profile': (*old, AccessProfile('Other', 'other', 'admin', 'test-only')),
        }
        for label, profiles in variants.items():
            for revision in (1, 2):
                with self.subTest(case=label, revision=revision), tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / 'synthetic-profiles.dat'
                    data = {'version': 1, 'defaults_revision': revision,
                            'profiles': [asdict(profile) for profile in profiles]}
                    original = MAGIC + json.dumps(data).encode()
                    path.write_bytes(original)
                    loaded = AccessStore(path, crypt=lambda value, **_: value).load()
                    self.assertEqual(loaded.profiles, profiles)
                    self.assertEqual(path.read_bytes(), original)

    def test_saved_revision_three_prevents_removed_elphapex_profile_from_resurrecting(self):
        from dataclasses import asdict
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic-profiles.dat'
            path.write_bytes(MAGIC + json.dumps({'version': 1, 'defaults_revision': 2,
                'profiles': [asdict(profile) for profile in self.former_defaults()]}).encode())
            store = AccessStore(path, crypt=lambda value, **_: value)
            self.assertEqual(store.load().profiles, standard_profiles())
            # Simulate the user's explicit removal followed by Save.
            store.save(AccessProfiles(self.former_defaults()))
            saved = json.loads(path.read_bytes()[len(MAGIC):])
            self.assertEqual(saved['defaults_revision'], 3)
            self.assertEqual(store.load().profiles, self.former_defaults())
            store.save(AccessProfiles([]))
            self.assertEqual(store.load().profiles, ())

    def test_current_or_future_defaults_revision_never_adds_a_missing_family(self):
        from dataclasses import asdict
        for revision in (3, 4):
            with self.subTest(revision=revision), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'synthetic-profiles.dat'
                original = MAGIC + json.dumps({'version': 1, 'defaults_revision': revision,
                    'profiles': [asdict(profile) for profile in self.former_defaults()]}).encode()
                path.write_bytes(original)
                self.assertEqual(AccessStore(path, crypt=lambda value, **_: value).load().profiles,
                                 self.former_defaults())
                self.assertEqual(path.read_bytes(), original)

    def test_invalid_defaults_revision_fails_closed_without_migration_or_write(self):
        from dataclasses import asdict
        for revision in (None, False, 0, '2', 2.5):
            with self.subTest(revision=revision), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'synthetic-profiles.dat'
                original = MAGIC + json.dumps({'version': 1, 'defaults_revision': revision,
                    'profiles': [asdict(profile) for profile in self.former_defaults()]}).encode()
                path.write_bytes(original)
                with self.assertRaises(ValueError):
                    AccessStore(path, crypt=lambda value, **_: value).load()
                self.assertEqual(path.read_bytes(), original)
