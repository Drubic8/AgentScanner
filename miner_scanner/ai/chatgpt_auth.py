"""Official ChatGPT account sign-in for the local, public-client application.

Tokens only cross the fixed HTTPS auth origin and protected store. The browser
uses a short-lived loopback authorization-code callback with state, nonce and
PKCE; a failed attempt leaves the active account unchanged.
"""
import base64
from datetime import datetime, timezone
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import re
import secrets
from threading import Event
import time
from urllib.parse import parse_qs, urlencode, urlsplit

try:
    import jwt
except ImportError:  # Core-only installs can still scan without desktop AI.
    jwt = None

from .contracts import AnalysisError
from .https_transport import request_bytes


ISSUER = "https://auth.openai.com"
AUTH_HOST = "auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
_AUTHORIZE = "/api/accounts/authorize"
_TOKEN = "/api/accounts/oauth/token"
_DISCOVERY = "/.well-known/openid-configuration"
_DIRECT = "chatgpt.tokens.use.direct"
_SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
_CLIENT = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
_SAFE_PATH = re.compile(r"/[A-Za-z0-9/_.-]{1,255}\Z")
_TERMINAL = {"invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired",
             "refresh_token_invalidated", "refresh_token_reused"}


def _fail(code):
    raise AnalysisError(code) from None


def _require_crypto():
    if jwt is None or not getattr(getattr(jwt, "algorithms", None), "has_crypto", False):
        _fail("auth_dependency_missing")


def _phase(callback, name):
    # Status delivery is observational; a disappearing widget must not change
    # the authorization protocol or expose URL/token details in exceptions.
    if callable(callback):
        try:
            callback(name)
        except Exception:
            pass


class _Budget:
    def __init__(self, cancel, seconds):
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= 600:
            _fail("configuration")
        self.cancel = cancel if cancel is not None else Event()
        self.deadline = time.monotonic() + seconds

    def remaining(self):
        if self.cancel.is_set():
            _fail("cancelled")
        value = self.deadline - time.monotonic()
        if value <= 0:
            _fail("timeout")
        return value


def _json(reply):
    try:
        value = json.loads(reply.body)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _fail("invalid_response")


def _http(method, path, budget, *, values=None, maximum=128 * 1024):
    headers = {"Accept": "application/json", "User-Agent": "ASIC-Monitor-ChatGPT"}
    body = b""
    if values is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        body = urlencode(values).encode("ascii")
    return request_bytes(AUTH_HOST, method, path, body, headers, cancel=budget.cancel,
                         seconds=min(20, budget.remaining()), max_bytes=maximum)


def _auth_path(url):
    if not isinstance(url, str):
        _fail("invalid_response")
    try:
        value = urlsplit(url)
        valid = (value.scheme == "https" and value.hostname == AUTH_HOST and value.port in (None, 443)
                 and not (value.username or value.password or value.query or value.fragment)
                 and _SAFE_PATH.fullmatch(value.path) and "//" not in value.path
                 and not any(part in (".", "..") for part in value.path.split("/")))
    except ValueError:
        valid = False
    if not valid:
        _fail("invalid_response")
    return value.path


def _metadata(budget):
    reply = _http("GET", _DISCOVERY, budget)
    if reply.status != 200:
        _http_error(reply)
    data = _json(reply)
    if data.get("issuer") != ISSUER:
        _fail("auth_failed")
    for field, expected in (("authorization_endpoint", _AUTHORIZE), ("token_endpoint", _TOKEN)):
        if field in data and _auth_path(data[field]) != expected:
            _fail("auth_failed")
    return {"jwks": _auth_path(data.get("jwks_uri")),
            "revoke": _auth_path(data.get("revocation_endpoint"))}


def _http_error(reply):
    _fail({400: "auth_failed", 401: "auth_required", 403: "forbidden", 429: "quota"}.get(
        reply.status, "server" if 500 <= reply.status <= 599 else "invalid_response"))


def _credential(value, *, optional=False, maximum=32 * 1024):
    if optional and value in (None, ""):
        return ""
    if (not isinstance(value, str) or not value or len(value) > maximum or not value.isascii()
            or any(ord(char) < 33 or ord(char) == 127 for char in value)):
        _fail("invalid_response")
    return value


def _verify_id(token, client_id, metadata, budget, *, nonce=None, subject=None):
    _require_crypto()
    token = _credential(token)
    try:
        header = jwt.get_unverified_header(token)
        algorithm = header.get("alg")
        required_type = {"RS256": "RSA", "ES256": "EC"}.get(algorithm)
        kid = header.get("kid")
        if required_type is None or not isinstance(kid, str) or not 0 < len(kid) <= 256:
            raise ValueError()
        if any(key in header for key in ("jku", "jwk", "x5u")):
            raise ValueError()
    except (ValueError, TypeError, jwt.PyJWTError):
        _fail("auth_failed")
    reply = _http("GET", metadata["jwks"], budget)
    if reply.status != 200:
        _http_error(reply)
    keys = _json(reply).get("keys")
    if not isinstance(keys, list) or not 0 < len(keys) <= 64:
        _fail("auth_failed")
    matching = [key for key in keys if isinstance(key, dict) and key.get("kid") == kid]
    if len(matching) != 1:
        _fail("auth_failed")
    key = matching[0]
    operations = key.get("key_ops", ["verify"])
    if (key.get("kty") != required_type or key.get("alg", algorithm) != algorithm
            or key.get("use", "sig") != "sig" or "d" in key
            or not isinstance(operations, list) or "verify" not in operations):
        _fail("auth_failed")
    try:
        public_key = jwt.PyJWK.from_dict(key, algorithm=algorithm).key
        claims = jwt.decode(token, public_key, algorithms=[algorithm], issuer=ISSUER,
                            audience=client_id, leeway=15,
                            options={"require": ["iss", "sub", "aud", "exp", "iat"]})
        if (not isinstance(claims.get("sub"), str) or not claims["sub"] or len(claims["sub"]) > 256
                or type(claims.get("exp")) is not int or type(claims.get("iat")) is not int):
            raise ValueError()
        if nonce is not None and (not isinstance(claims.get("nonce"), str)
                or not hmac.compare_digest(claims["nonce"].encode(), nonce.encode())):
            raise ValueError()
        if subject is not None and claims["sub"] != subject:
            raise ValueError()
        audiences = claims["aud"]
        if ((isinstance(audiences, list) and len(audiences) > 1 and claims.get("azp") != client_id)
                or claims.get("azp", client_id) != client_id):
            raise ValueError()
        email = claims.get("email", "")
        if not isinstance(email, str) or len(email) > 320 or any(ord(char) < 32 for char in email):
            raise ValueError()
        budget.remaining()
        return claims
    except (ValueError, TypeError, KeyError, jwt.PyJWTError):
        _fail("auth_failed")


def _scopes(value):
    if not isinstance(value, str) or len(value) > 4096 or not value.isascii():
        _fail("invalid_response")
    values = value.split()
    if len(values) > 32 or any(not re.fullmatch(r"[A-Za-z0-9._:/-]{1,128}", item) for item in values):
        _fail("invalid_response")
    return list(dict.fromkeys(values))


def _session(data, client_id, host_id, metadata, budget, *, nonce=None, previous=None):
    scopes = _scopes(data.get("scope"))
    access = _credential(data.get("access_token"), optional=_DIRECT not in scopes)
    refresh = _credential(data.get("refresh_token"), optional=True)
    if previous is not None and not refresh:
        _fail("invalid_response")  # This flow rotates the renewable token.
    token_type = data.get("token_type", "Bearer")
    if not isinstance(token_type, str) or token_type.lower() != "bearer":
        _fail("invalid_response")
    lifetime = data.get("expires_in", 0 if not access else None)
    if type(lifetime) not in (int, float) or not math.isfinite(lifetime) or not 0 <= lifetime <= 86400:
        _fail("invalid_response")
    if access and lifetime <= 0:
        _fail("invalid_response")
    identity_token = data.get("id_token")
    if identity_token:
        claims = _verify_id(identity_token, client_id, metadata, budget, nonce=nonce,
                            subject=previous["subject"] if previous else None)
    elif previous is not None:
        # The protected store holds the previously signature-verified identity.
        # An expired retained ID token is only a future login hint, not proof
        # authorizing a refresh or a model request.
        identity_token = previous["id_token"]
        claims = {"sub": previous["subject"], "email": previous["email"]}
    else:
        _fail("auth_failed")
    return dict(issuer=ISSUER, subject=claims["sub"], email=claims.get("email", ""),
                client_id=client_id, ext_agent_host_id=host_id,
                id_token=identity_token, access_token=access, refresh_token=refresh,
                token_type="Bearer", scopes=scopes, expires_at=time.time() + lifetime,
                saved_at=datetime.now(timezone.utc).isoformat())


class _Loopback(HTTPServer):
    allow_reuse_address = False
    request_queue_size = 4

    def __init__(self, state):
        self.state = state
        self.result = None
        super().__init__(("127.0.0.1", 0), _Callback)
        self.timeout = 0.15


class _Callback(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(0.5)

    def log_message(self, *_):
        pass  # OAuth authorization codes and hints must never reach stderr.

    def _reply(self, status):
        body = b"Return to ASIC Monitor to continue." if status == 200 else b"Invalid callback."
        try:
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass

    def do_GET(self):
        try:
            parsed = urlsplit(self.path)
            if (len(self.path) > 16 * 1024 or parsed.path != "/auth/callback" or parsed.scheme
                    or parsed.netloc or parsed.fragment or self.server.result is not None
                    or self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}"):
                raise ValueError()
            params = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=12)
            if any(len(values) != 1 for values in params.values()):
                raise ValueError()
            values = {key: value[0] for key, value in params.items()}
            state = values.get("state", "")
            if not state.isascii() or not hmac.compare_digest(state, self.server.state):
                raise ValueError()
            if "iss" in values and values["iss"] != ISSUER:
                raise ValueError()
            if "error" in values:
                if "code" in values:
                    raise ValueError()
                self.server.result = {"error": "auth_denied" if values["error"] == "access_denied" else "auth_failed"}
            else:
                values["code"] = _credential(values.get("code"), maximum=4096)
                self.server.result = values
            self._reply(200)
        except (ValueError, TypeError, AnalysisError):
            self._reply(400)

    def do_POST(self):
        self._reply(405)


def _client(value):
    if not isinstance(value, str) or not _CLIENT.fullmatch(value) or value == "dynamic_agent_client":
        _fail("auth_failed")
    return value


def sign_in(store, *, open_browser, cancel=None, timeout_seconds=180,
            new_account=False, client_id=None, on_phase=None):
    budget = _Budget(cancel, timeout_seconds)
    budget.remaining()
    _phase(on_phase, "preparing")
    _require_crypto()
    with store.locked(cancel=budget.cancel):
        previous = None if new_account else store.get(client_id) if client_id else store.load()
        if client_id and not new_account and previous is None:
            _fail("auth_required")
        host_id = store.host_id()
    selected_client = _client(previous["client_id"]) if previous else None
    state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    try:
        server = _Loopback(state)
    except OSError:
        _fail("network")
    try:
        redirect = f"http://127.0.0.1:{server.server_port}/auth/callback"
        query = dict(client_id=selected_client or "dynamic_agent_client", ext_agent_host_id=host_id,
                     response_type="code", redirect_uri=redirect, scope=_SCOPES, resource=RESOURCE,
                     state=state, nonce=nonce, code_challenge_method="S256", code_challenge=challenge)
        if selected_client:
            if previous.get("id_token"):
                query["id_token_hint"] = previous["id_token"]
            if previous.get("email"):
                query["login_hint"] = previous["email"]
            if _DIRECT not in previous.get("scopes", []):
                # This explicit retry follows identity-only consent. Ordinary
                # returning sign-ins keep their existing consent behavior.
                query["prompt"] = "consent"
        else:
            query["agent_name_hint"] = "ASIC Monitor"
        budget.remaining()
        try:
            opened = open_browser(ISSUER + _AUTHORIZE + "?" + urlencode(query))
        except Exception:
            _fail("browser_failed")
        if opened is False:
            _fail("browser_failed")
        _phase(on_phase, "waiting_browser")
        while server.result is None:
            budget.remaining()
            server.handle_request()
        budget.remaining()
        callback = server.result
    finally:
        server.server_close()
    if "error" in callback:
        _fail(callback["error"])
    issued = _client(callback.get("client_id")) if not selected_client else selected_client
    if selected_client and callback.get("client_id", selected_client) != selected_client:
        _fail("auth_failed")
    _phase(on_phase, "validating")
    # Authorization uses a fixed documented endpoint. Discovery is needed for
    # trusted JWT/revocation metadata only after the browser has returned a
    # valid code; network delay must never silently postpone opening it.
    metadata = _metadata(budget)
    reply = _http("POST", _TOKEN, budget, values=dict(grant_type="authorization_code",
        client_id=issued, code=callback["code"], code_verifier=verifier, redirect_uri=redirect, resource=RESOURCE))
    if reply.status != 200:
        _http_error(reply)
    session = _session(_json(reply), issued, host_id, metadata, budget, nonce=nonce, previous=None)
    if previous and session["subject"] != previous["subject"]:
        _fail("auth_failed")
    with store.locked(cancel=budget.cancel):
        budget.remaining()
        store.save(session)
    return session


def ensure_access(store, *, cancel=None):
    budget = _Budget(cancel, 45)
    with store.locked(cancel=budget.cancel):
        session = store.load()
        if not session or not session.get("access_token"):
            _fail("auth_required")
        if _DIRECT not in session.get("scopes", []):
            _fail("permission_required")
        budget.remaining()
        if session["expires_at"] > time.time() + 60:
            return session
        if not session.get("refresh_token"):
            _fail("auth_required")
        _require_crypto()
        metadata = _metadata(budget)
        reply = _http("POST", _TOKEN, budget, values=dict(grant_type="refresh_token",
            client_id=_client(session["client_id"]), refresh_token=session["refresh_token"], resource=RESOURCE))
        if reply.status != 200:
            data = _json(reply)
            if data.get("error") in _TERMINAL:
                store.clear_tokens(session["client_id"])
                _fail("auth_required")
            _http_error(reply)
        try:
            renewed = _session(_json(reply), session["client_id"], session["ext_agent_host_id"],
                               metadata, budget, previous=session)
        except AnalysisError as exc:
            if exc.code not in ("cancelled", "timeout", "network", "server", "auth_dependency_missing"):
                store.clear_tokens(session["client_id"])
            raise
        budget.remaining()
        store.save(renewed)
        if _DIRECT not in renewed["scopes"]:
            _fail("permission_required")
        return renewed


def sign_out(store, *, cancel=None):
    budget = _Budget(cancel, 30)
    confirmed = False
    with store.locked(cancel=budget.cancel):
        session = store.load()
        if not session:
            return True
        if not any(session.get(key) for key in ("access_token", "refresh_token", "id_token")):
            return True
        try:
            if session.get("refresh_token"):
                metadata = _metadata(budget)
                reply = _http("POST", metadata["revoke"], budget, values=dict(
                    token=session["refresh_token"], token_type_hint="refresh_token", client_id=_client(session["client_id"])))
                confirmed = reply.status == 200 and not reply.body.strip()
        except AnalysisError:
            confirmed = False
        finally:
            store.clear_tokens(session["client_id"])
    return confirmed
