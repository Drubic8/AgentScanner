"""Account isolation, renewable-session persistence and DPAPI boundaries."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from desktop_ui.chatgpt_store import ChatGPTStore


def crypt(data, *, decrypt=False):
    return bytes(value ^ 0x53 for value in data)


def session(client='oaiapp_demo1', subject='subject-1'):
    return dict(issuer='https://auth.openai.com', subject=subject, email='demo@example.invalid',
        client_id=client, ext_agent_host_id='urn:uuid:aca9f139-b8bd-40f3-bf4e-7a8185ddcf22',
        access_token='synthetic-access-secret', refresh_token='synthetic-refresh-secret',
        id_token='synthetic.id.token', token_type='Bearer', scopes=['chatgpt.tokens.use.direct'],
        expires_at=9999999999, saved_at='2026-11-01T10:00:00Z')


class ChatGPTStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'chatgpt_credentials.dat'
        self.store = ChatGPTStore(self.path, crypt=crypt)

    def test_missing_account_and_open_do_not_create_session(self):
        self.assertIsNone(self.store.load())
        self.assertEqual(self.store.accounts(), [])
        self.assertFalse(self.path.exists())

    def test_accounts_with_same_email_stay_separate_and_public_view_hides_tokens(self):
        first, second = session(), session('oaiapp_demo2', 'subject-2')
        self.store.save(first)
        self.store.save(second)
        self.assertEqual(len(self.store.accounts()), 2)
        self.assertEqual(self.store.load()['client_id'], second['client_id'])
        self.store.select(first['client_id'])
        self.assertEqual(self.store.load()['subject'], first['subject'])
        for secret in ('synthetic-access-secret', 'synthetic-refresh-secret', 'synthetic.id.token'):
            self.assertNotIn(secret.encode(), self.path.read_bytes())
            self.assertNotIn(secret, json.dumps(self.store.accounts()))
            self.assertNotIn(secret, repr(self.store))

    def test_refresh_keeps_welcome_and_replaces_token_set_together(self):
        self.store.save(session())
        self.store.mark_welcome('oaiapp_demo1')
        refreshed = session()
        refreshed.update(access_token='new-synthetic-access', refresh_token='new-synthetic-refresh')
        with self.store.locked():
            self.store.save(refreshed)
        self.assertTrue(self.store.has_welcome('oaiapp_demo1'))
        self.assertEqual(self.store.load()['access_token'], 'new-synthetic-access')
        self.assertEqual(self.store.load()['refresh_token'], 'new-synthetic-refresh')

    def test_different_identity_cannot_replace_existing_registration(self):
        self.store.save(session())
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.save(session(subject='wrong-subject'))
        self.assertEqual(self.path.read_bytes(), before)

    def test_signout_keeps_mapping_host_and_other_account(self):
        self.store.save(session())
        self.store.save(session('oaiapp_demo2', 'subject-2'))
        host = self.store.host_id()
        self.store.clear_tokens('oaiapp_demo2')
        self.assertEqual(self.store.load()['access_token'], '')
        self.assertEqual(self.store.get('oaiapp_demo1')['access_token'], 'synthetic-access-secret')
        self.assertEqual(self.store.host_id(), host)
        self.assertEqual(ChatGPTStore(self.path, crypt=crypt).host_id(), host)
        self.assertFalse(self.store.accounts()[1]['enabled'])

    def test_corrupted_encrypted_data_or_host_do_not_silently_reset_identity(self):
        self.store.save(session())
        self.path.write_bytes(b'corrupted')
        with self.assertRaises(ValueError):
            self.store.load()
        self.path.with_name('.chatgpt-host.json').write_text('{}', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.store.host_id()

    def test_protection_failure_preserves_previous_session(self):
        self.store.save(session())
        before = self.path.read_bytes()
        def failed(data, **kwargs):
            raise RuntimeError('synthetic-access-secret')
        store = ChatGPTStore(self.path, crypt=failed)
        with self.assertRaises((ValueError, OSError)) as caught:
            store.save(session())
        self.assertNotIn('synthetic-access-secret', str(caught.exception))
        self.assertEqual(self.path.read_bytes(), before)
