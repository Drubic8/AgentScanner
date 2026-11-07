"""OAuth fixtures use generated RSA keys, fake HTTPS, and loopback callbacks only."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import base64
import http.client
import json
import os
from pathlib import Path
import socket
import tempfile
from threading import Event, RLock, Thread
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

try:
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
except ImportError:
    jwt = None

from miner_scanner.ai import chatgpt_auth as auth
from miner_scanner.ai.contracts import AnalysisError


def reply(data=None, status=200):
    return SimpleNamespace(status=status, headers={"content-type": "application/json"},
                           body=b"" if data is None else json.dumps(data).encode())


class MemoryStore:
    def __init__(self, record=None):
        self.active = deepcopy(record)
        self.records = {record["client_id"]: deepcopy(record)} if record else {}
        self.saves = 0
        self.clears = 0
        self.lock = RLock()

    @contextmanager
    def locked(self, cancel=None):
        with self.lock:
            if cancel is not None and cancel.is_set():
                raise AnalysisError("cancelled")
            yield self

    def load(self):
        return deepcopy(self.active)

    def get(self, client_id):
        return deepcopy(self.records.get(client_id))

    def host_id(self):
        return "urn:uuid:10000000-0000-0000-0000-000000000001"

    def save(self, record):
        self.active = deepcopy(record)
        self.records[record["client_id"]] = deepcopy(record)
        self.saves += 1

    def clear_tokens(self, client_id=None):
        target = client_id or self.active["client_id"]
        record = self.records[target]
        for name in ("access_token", "refresh_token", "id_token"):
            record[name] = ""
        record["scopes"] = []
        record["expires_at"] = 0
        if self.active["client_id"] == target:
            self.active = deepcopy(record)
        self.clears += 1


@unittest.skipUnless(jwt is not None, "PyJWT crypto extra is required for account sign-in")
class ChatGPTAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls.key.public_key()))
        cls.jwk.update(kid="test-signing-key", use="sig", alg="RS256")

    def setUp(self):
        self.store = MemoryStore()
        self.calls = []
        self.browser_query = None
        self.callback_threads = []
        self.callback_statuses = []
        self.callback_overrides = {}
        self.claim_overrides = {}
        self.token_overrides = {}
        self.token_status = 200
        self.token_error = None
        self.signing_key = self.key
        self.metadata = dict(issuer=auth.ISSUER,
            authorization_endpoint=auth.ISSUER + "/api/accounts/authorize",
            token_endpoint=auth.ISSUER + "/api/accounts/oauth/token",
            jwks_uri=auth.ISSUER + "/.well-known/jwks.json",
            revocation_endpoint=auth.ISSUER + "/api/accounts/oauth/revoke")
        self.http = patch.object(auth, "request_bytes", side_effect=self.request)
        self.http.start()
        self.addCleanup(self.http.stop)
        self.addCleanup(self.join_callbacks)

    def join_callbacks(self):
        for thread in self.callback_threads:
            thread.join(2)
            self.assertFalse(thread.is_alive())

    def identity(self, *, nonce=None, client="oaiapp_test", subject="subject-test", **changes):
        now = int(time.time())
        claims = dict(iss=auth.ISSUER, sub=subject, aud=client, iat=now, exp=now + 3600,
                      email="demo@example.invalid")
        if nonce is not None:
            claims["nonce"] = nonce
        claims.update(changes)
        return jwt.encode(claims, self.signing_key, algorithm="RS256", headers={"kid": "test-signing-key"})

    def token(self, *, nonce=None, client="oaiapp_test", subject="subject-test"):
        identity = dict(nonce=nonce, client=client, subject=subject)
        identity.update(self.claim_overrides)
        value = dict(id_token=self.identity(**identity),
                     access_token="synthetic-access-token", refresh_token="synthetic-refresh-token",
                     token_type="Bearer", expires_in=3600, scope=auth._SCOPES)
        value.update(self.token_overrides)
        return value

    def record(self, *, expires=None):
        return dict(issuer=auth.ISSUER, subject="subject-test", email="demo@example.invalid",
                    client_id="oaiapp_test", ext_agent_host_id=self.store.host_id(),
                    id_token=self.identity(), access_token="old-access", refresh_token="old-refresh",
                    token_type="Bearer", scopes=auth._SCOPES.split(),
                    expires_at=time.time() + 3600 if expires is None else expires, saved_at="2026-11-01T00:00:00+00:00")

    def request(self, host, method, path, body, headers, **kwargs):
        self.assertEqual(host, "auth.openai.com")
        self.assertLessEqual(kwargs["seconds"], 20)
        self.calls.append((method, path, body, headers))
        if path == "/.well-known/openid-configuration":
            return reply(self.metadata)
        if path == "/.well-known/jwks.json":
            return reply({"keys": [self.jwk]})
        if path == "/api/accounts/oauth/token":
            if self.token_error:
                raise self.token_error
            if self.token_status != 200:
                return reply({"error": self.token_overrides.get("error", "temporarily_unavailable")}, self.token_status)
            values = {name: items[0] for name, items in parse_qs(body.decode()).items()}
            nonce = self.browser_query["nonce"][0] if values["grant_type"] == "authorization_code" else None
            return reply(self.token(nonce=nonce, client=values["client_id"]))
        if path == "/api/accounts/oauth/revoke":
            return reply()
        self.fail("Unexpected scoped HTTPS path")

    def browser(self, url, *, callbacks=None):
        parsed = urlsplit(url)
        self.assertEqual(parsed.scheme + "://" + parsed.netloc, auth.ISSUER)
        self.assertEqual(parsed.path, "/api/accounts/authorize")
        self.browser_query = parse_qs(parsed.query)
        redirect = urlsplit(self.browser_query["redirect_uri"][0])
        self.assertEqual(redirect.hostname, "127.0.0.1")
        self.assertEqual(redirect.path, "/auth/callback")
        self.callback_port = redirect.port
        # Listener must already be bound when the browser is opened.
        with socket.create_connection(("127.0.0.1", redirect.port), timeout=1):
            pass
        def complete():
            values = dict(state=self.browser_query["state"][0], code="synthetic-code", client_id="oaiapp_test")
            values.update(self.callback_overrides)
            actions = callbacks or [("GET", "/auth/callback?" + urlencode(values), None)]
            for method, path, host in actions:
                connection = http.client.HTTPConnection("127.0.0.1", redirect.port, timeout=2)
                try:
                    connection.request(method, path, headers={"Host": host} if host else {})
                    response = connection.getresponse()
                    self.callback_statuses.append(response.status)
                    response.read()
                finally:
                    connection.close()
        thread = Thread(target=complete)
        self.callback_threads.append(thread)
        thread.start()

    def test_dynamic_signin_uses_issued_client_pkce_nonce_and_closes_listener(self):
        session = auth.sign_in(self.store, open_browser=self.browser)
        self.join_callbacks()
        query = self.browser_query
        self.assertEqual(query["client_id"], ["dynamic_agent_client"])
        self.assertEqual(query["agent_name_hint"], ["ASIC Monitor"])
        self.assertEqual(query["resource"], [auth.RESOURCE])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        post = next(item for item in self.calls if item[0] == "POST")
        values = parse_qs(post[2].decode())
        challenge = base64.urlsafe_b64encode(hashlib.sha256(values["code_verifier"][0].encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(query["code_challenge"], [challenge])
        self.assertEqual(values["client_id"], ["oaiapp_test"])
        self.assertEqual(values["redirect_uri"], query["redirect_uri"])
        self.assertNotIn("client_secret", values)
        self.assertEqual(session["subject"], "subject-test")
        self.assertEqual(session["client_id"], "oaiapp_test")
        self.assertEqual(self.store.saves, 1)
        self.assertEqual(self.callback_statuses, [200])
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", self.callback_port), timeout=0.2)

    def test_returning_signin_preserves_client_and_rejects_other_identity(self):
        before = self.record()
        self.store = MemoryStore(before)
        auth.sign_in(self.store, open_browser=self.browser)
        self.assertEqual(self.browser_query["client_id"], ["oaiapp_test"])
        self.assertEqual(self.browser_query["id_token_hint"], [before["id_token"]])
        self.assertNotIn("agent_name_hint", self.browser_query)
        self.assertNotIn("prompt", self.browser_query)
        self.join_callbacks()
        self.claim_overrides = {"sub": "another-user"}
        current = self.store.load()
        with self.assertRaises(AnalysisError) as error:
            auth.sign_in(self.store, open_browser=self.browser)
        self.assertEqual(error.exception.code, "auth_failed")
        self.assertEqual(self.store.load(), current)

    def test_new_account_is_separate_until_identity_validates(self):
        before = self.record()
        self.store = MemoryStore(before)
        self.claim_overrides = {"nonce": "wrong-nonce"}
        with self.assertRaises(AnalysisError):
            auth.sign_in(self.store, open_browser=self.browser, new_account=True)
        self.assertEqual(self.browser_query["client_id"], ["dynamic_agent_client"])
        self.assertEqual(self.store.load(), before)
        self.assertEqual(self.store.saves, 0)

    def test_bad_signature_nonce_issuer_audience_and_expiration_never_save(self):
        cases = [{"nonce": "wrong"}, {"iss": "https://other.invalid"}, {"aud": "other-client"},
                 {"exp": int(time.time()) - 120}]
        for changes in cases:
            self.claim_overrides = changes
            with self.subTest(claim=list(changes)), self.assertRaises(AnalysisError) as error:
                auth.sign_in(self.store, open_browser=self.browser)
            self.assertEqual(error.exception.code, "auth_failed")
            self.assertEqual(self.store.saves, 0)
            self.join_callbacks()
        self.claim_overrides = {}
        self.signing_key = self.other_key
        with self.assertRaises(AnalysisError):
            auth.sign_in(self.store, open_browser=self.browser)
        self.assertEqual(self.store.saves, 0)

    def test_no_direct_permission_retains_identity_but_blocks_inference(self):
        self.token_overrides = {"scope": "openid profile email"}
        session = auth.sign_in(self.store, open_browser=self.browser)
        self.assertEqual(session["subject"], "subject-test")
        self.assertNotIn("chatgpt.tokens.use.direct", session["scopes"])
        with self.assertRaises(AnalysisError) as error:
            auth.ensure_access(self.store)
        self.assertEqual(error.exception.code, "permission_required")
        self.join_callbacks()
        auth.sign_in(self.store, open_browser=self.browser)
        self.assertEqual(self.browser_query["prompt"], ["consent"])

    def test_explicit_continue_after_local_token_clear_requests_consent(self):
        self.store = MemoryStore(self.record())
        self.store.clear_tokens()
        auth.sign_in(self.store, open_browser=self.browser)
        self.assertEqual(self.browser_query["client_id"], ["oaiapp_test"])
        self.assertEqual(self.browser_query["prompt"], ["consent"])
        self.assertNotIn("id_token_hint", self.browser_query)
        self.assertIn("chatgpt.tokens.use.direct", self.browser_query["scope"][0].split())

    def test_access_denied_and_missing_issued_client_do_not_exchange_code(self):
        def denied(url):
            self.browser_query = parse_qs(urlsplit(url).query)
            values = dict(error="access_denied", state=self.browser_query["state"][0])
            self.browser(url, callbacks=[("GET", "/auth/callback?" + urlencode(values), None)])
        with self.assertRaises(AnalysisError) as error:
            auth.sign_in(self.store, open_browser=denied)
        self.assertEqual(error.exception.code, "auth_denied")
        self.assertFalse(any(method == "POST" for method, *_ in self.calls))
        self.join_callbacks()
        self.callback_overrides = {"client_id": "dynamic_agent_client"}
        with self.assertRaises(AnalysisError):
            auth.sign_in(self.store, open_browser=self.browser)
        self.assertFalse(any(method == "POST" for method, *_ in self.calls))

    def test_invalid_callback_method_path_host_state_and_duplicates_are_ignored(self):
        def guarded(url):
            query = parse_qs(urlsplit(url).query)
            values = dict(state=query["state"][0], code="synthetic-code", client_id="oaiapp_test")
            valid = "/auth/callback?" + urlencode(values)
            self.browser(url, callbacks=[("POST", valid, None), ("GET", "/wrong?" + urlencode(values), None),
                ("GET", valid, "other.invalid"), ("GET", valid + "&state=duplicate", None),
                ("GET", "/auth/callback?" + urlencode({**values, "state": "wrong"}), None),
                ("GET", valid, None)])
        auth.sign_in(self.store, open_browser=guarded)
        self.join_callbacks()
        self.assertEqual(self.callback_statuses, [405, 400, 400, 400, 400, 200])
        self.assertEqual(sum(method == "POST" for method, *_ in self.calls), 1)

    def test_cancel_timeout_or_browser_failure_closes_callback_and_keeps_active(self):
        before = self.record()
        for mode in ("cancel", "timeout", "browser"):
            self.store = MemoryStore(before)
            event = Event()
            ports = []
            def browser(url):
                port = urlsplit(parse_qs(urlsplit(url).query)["redirect_uri"][0]).port
                ports.append(port)
                if mode == "cancel":
                    event.set()
                return False if mode == "browser" else None
            with self.subTest(mode=mode), self.assertRaises(AnalysisError) as error:
                auth.sign_in(self.store, open_browser=browser, cancel=event, timeout_seconds=0.2)
            self.assertEqual(error.exception.code, {"cancel": "cancelled", "timeout": "timeout", "browser": "browser_failed"}[mode])
            self.assertEqual(self.store.load(), before)
            with self.assertRaises(OSError):
                socket.create_connection(("127.0.0.1", ports[0]), timeout=0.2)

    def test_discovery_cannot_route_jwks_or_revocation_off_auth_origin(self):
        for field in ("jwks_uri", "revocation_endpoint"):
            original = self.metadata[field]
            self.metadata[field] = "https://other.invalid/token-leak"
            with self.subTest(field=field), self.assertRaises(AnalysisError) as error:
                auth.sign_in(self.store, open_browser=self.browser)
            self.assertEqual(error.exception.code, "invalid_response")
            self.assertFalse(any(method == "POST" for method, *_ in self.calls))
            self.join_callbacks()
            self.metadata[field] = original

    def test_browser_opens_before_any_discovery_even_when_python_network_fails(self):
        before = self.record()
        self.store = MemoryStore(before)
        opened, phases = [], []
        def browser(url):
            self.assertFalse(self.calls)
            opened.append(True)
            self.browser(url)
        def unavailable(*args, **kwargs):
            self.assertTrue(opened)
            self.calls.append((args[1], args[2], args[3], args[4]))
            raise AnalysisError("network")
        with patch.object(auth, "request_bytes", side_effect=unavailable):
            with self.assertRaises(AnalysisError) as error:
                auth.sign_in(self.store, open_browser=browser, on_phase=phases.append)
        self.assertEqual(error.exception.code, "network")
        self.assertEqual(phases, ["preparing", "waiting_browser", "validating"])
        self.assertEqual(opened, [True])
        self.assertEqual(self.store.load(), before)
        self.assertFalse(any(method == "POST" for method, *_ in self.calls))
        self.join_callbacks()
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", self.callback_port), timeout=0.2)

    def test_missing_jwt_or_crypto_is_explicit_before_browser_or_network(self):
        incomplete = SimpleNamespace(algorithms=SimpleNamespace(has_crypto=False))
        for dependency in (None, incomplete):
            with self.subTest(missing="jwt" if dependency is None else "crypto"), \
                    patch.object(auth, "jwt", dependency), self.assertRaises(AnalysisError) as error:
                auth.sign_in(self.store, open_browser=lambda _: self.fail("Missing dependency must not open browser"))
            self.assertEqual(error.exception.code, "auth_dependency_missing")
            self.assertFalse(self.calls)

    def test_missing_crypto_does_not_refresh_or_erase_existing_session(self):
        before = self.record(expires=time.time() - 1)
        self.store = MemoryStore(before)
        with patch.object(auth, "jwt", None), self.assertRaises(AnalysisError) as error:
            auth.ensure_access(self.store)
        self.assertEqual(error.exception.code, "auth_dependency_missing")
        self.assertEqual(self.store.load(), before)
        self.assertFalse(self.calls)

    def test_fresh_access_uses_protected_verified_record_without_extra_calls(self):
        self.store = MemoryStore(self.record())
        result = auth.ensure_access(self.store)
        self.assertEqual(result["access_token"], "old-access")
        self.assertFalse(self.calls)

    def test_refresh_rotates_atomically_under_lock_and_keeps_client_and_subject(self):
        expired = self.record(expires=time.time() - 100)
        self.store = MemoryStore(expired)
        results, errors = [], []
        def run():
            try:
                results.append(auth.ensure_access(self.store))
            except Exception as error:
                errors.append(error)
        workers = [Thread(target=run) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(2)
            self.assertFalse(worker.is_alive())
        self.assertFalse(errors)
        self.assertEqual(len(results), 2)
        posts = [item for item in self.calls if item[0] == "POST"]
        self.assertEqual(len(posts), 1)
        body = parse_qs(posts[0][2].decode())
        self.assertEqual(body["client_id"], [expired["client_id"]])
        self.assertEqual(body["refresh_token"], ["old-refresh"])
        self.assertNotIn("scope", body)
        self.assertEqual(self.store.load()["refresh_token"], "synthetic-refresh-token")

    def test_expired_retained_id_does_not_prevent_refresh_without_new_id_token(self):
        before = self.record(expires=time.time() - 100)
        before["id_token"] = self.identity(exp=int(time.time()) - 120)
        self.store = MemoryStore(before)
        self.token_overrides = {"id_token": None}
        session = auth.ensure_access(self.store)
        self.assertEqual(session["id_token"], before["id_token"])
        self.assertEqual(session["access_token"], "synthetic-access-token")

    def test_terminal_refresh_clears_tokens_but_transient_failure_preserves_them(self):
        before = self.record(expires=time.time() - 100)
        for status, code, clears in ((400, "invalid_grant", True), (503, "temporarily_unavailable", False)):
            self.store = MemoryStore(before)
            self.token_status, self.token_overrides = status, {"error": code}
            with self.subTest(status=status), self.assertRaises(AnalysisError):
                auth.ensure_access(self.store)
            self.assertEqual(bool(self.store.clears), clears)
            self.assertEqual(self.store.load()["client_id"], before["client_id"])
        self.token_status = 200
        self.token_error = AnalysisError("network")
        self.store = MemoryStore(before)
        with self.assertRaises(AnalysisError):
            auth.ensure_access(self.store)
        self.assertEqual(self.store.load(), before)

    def test_refresh_identity_switch_is_rejected_without_activating_another_user(self):
        self.store = MemoryStore(self.record(expires=time.time() - 100))
        self.claim_overrides = {"sub": "different-subject"}
        with self.assertRaises(AnalysisError) as error:
            auth.ensure_access(self.store)
        self.assertEqual(error.exception.code, "auth_failed")
        self.assertEqual(self.store.load()["subject"], "subject-test")
        self.assertEqual(self.store.load()["access_token"], "")

    def test_signout_revoke_and_unconfirmed_failure_both_clear_local_tokens(self):
        before = self.record()
        self.store = MemoryStore(before)
        self.assertTrue(auth.sign_out(self.store))
        revoke = next(item for item in self.calls if item[1].endswith("/revoke"))
        values = parse_qs(revoke[2].decode())
        self.assertEqual(values["token_type_hint"], ["refresh_token"])
        self.assertEqual(values["client_id"], [before["client_id"]])
        self.assertEqual(values["token"], [before["refresh_token"]])
        self.assertEqual(self.store.load()["access_token"], "")
        self.assertEqual(self.store.load()["subject"], before["subject"])
        self.store = MemoryStore(before)
        with patch.object(auth, "request_bytes", side_effect=AnalysisError("network")):
            self.assertFalse(auth.sign_out(self.store))
        self.assertEqual(self.store.load()["refresh_token"], "")

    def test_returning_callback_cannot_change_the_issued_client(self):
        before = self.record()
        self.store = MemoryStore(before)
        self.callback_overrides = {"client_id": "oaiapp_other"}
        with self.assertRaises(AnalysisError) as error:
            auth.sign_in(self.store, open_browser=self.browser)
        self.assertEqual(error.exception.code, "auth_failed")
        self.assertEqual(self.store.load(), before)
        self.assertFalse(any(method == "POST" for method, *_ in self.calls))

    def test_revocation_requires_documented_empty_200_reply(self):
        before = self.record()
        self.store = MemoryStore(before)
        original = self.request
        def request(*args, **kwargs):
            if args[2].endswith("/revoke"):
                return reply({"error": "not-revoked"})
            return original(*args, **kwargs)
        with patch.object(auth, "request_bytes", side_effect=request):
            self.assertFalse(auth.sign_out(self.store))
        self.assertEqual(self.store.load()["refresh_token"], "")

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI integration")
    def test_actual_protected_store_signin_refresh_welcome_and_signout_contract(self):
        from desktop_ui.chatgpt_store import ChatGPTStore
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chatgpt_sessions.dat"
            self.store = ChatGPTStore(path)
            session = auth.sign_in(self.store, open_browser=self.browser)
            self.join_callbacks()
            saved = self.store.load()
            self.assertEqual(saved["client_id"], session["client_id"])
            self.assertEqual(saved["subject"], session["subject"])
            self.assertTrue(self.store.accounts()[0]["enabled"])
            self.assertNotIn(b"synthetic-access-token", path.read_bytes())
            self.store.mark_welcome(saved["client_id"])
            saved["expires_at"] = time.time() - 1
            self.store.save(saved)
            self.token_overrides = {"refresh_token": "synthetic-rotated-refresh"}
            renewed = auth.ensure_access(self.store)
            self.assertEqual(renewed["refresh_token"], "synthetic-rotated-refresh")
            self.assertTrue(self.store.has_welcome(saved["client_id"]))
            self.assertTrue(auth.sign_out(self.store))
            self.assertFalse(self.store.accounts()[0]["enabled"])
            self.assertEqual(self.store.load()["subject"], session["subject"])


if __name__ == "__main__":
    unittest.main()
