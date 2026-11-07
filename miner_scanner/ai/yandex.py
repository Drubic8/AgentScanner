"""Explicit, bounded Yandex AI Studio calls without tools or automatic retries.

Protocol: https://aistudio.yandex.ru/ru/docs/ai-studio/api/Chat-Completions/createChatCompletion
The caller supplies a previewed, redacted PreparedAnalysis, never ASIC credentials.
"""
import json
import http.client
import socket
import ssl
import time
from threading import Event, Lock, Thread

from .contracts import AnalysisError, ProviderReply
from .settings import AnalysisConfig


ENDPOINT = "https://ai.api.cloud.yandex.net/v1/chat/completions"
_HOST = "ai.api.cloud.yandex.net"
_PATH = "/v1/chat/completions"
MAX_REQUEST_BYTES = 128 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
MAX_TEXT_CHARS = 64 * 1024


def _fail(code):
    raise AnalysisError(code) from None


def _cancelled(cancel):
    if cancel.is_set():
        _fail("cancelled")


def _token_count(value):
    return value if type(value) is int and 0 <= value < 100_000_000 else None


from .https_transport import RequestAbort as _RequestAbort, FixedHTTPSConnection


class _CancellableHTTPSConnection(FixedHTTPSConnection):
    def __init__(self, abort):
        super().__init__(_HOST, abort)


def _read_response(body, headers, cancel, seconds):
    connection = response = None
    try:
        with _RequestAbort(cancel, seconds) as abort:
            try:
                connection = _CancellableHTTPSConnection(abort)
                connection.request("POST", _PATH, body=body, headers=headers)
                abort.check()
                response = connection.getresponse()
                abort.check()
                status = response.status
                if status != 200:
                    _fail({401: "auth_required", 403: "forbidden", 429: "quota",
                           408: "timeout", 400: "invalid_request", 404: "model_unavailable"}.get(
                              status, "server" if 500 <= status <= 599 else "invalid_response"))
                content_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
                if content_type not in ("application/json", "application/problem+json"):
                    _fail("invalid_response")
                if response.getheader("Content-Encoding", "identity").strip().lower() != "identity":
                    _fail("invalid_response")
                data = bytearray()
                while True:
                    abort.check()
                    chunk = response.read(min(8192, MAX_RESPONSE_BYTES + 1 - len(data)))
                    abort.check()
                    if not chunk:
                        break
                    if len(data) + len(chunk) > MAX_RESPONSE_BYTES:
                        _fail("too_large")
                    data.extend(chunk)
                return bytes(data)
            except AnalysisError:
                raise
            except TimeoutError:
                abort.check()
                _fail("timeout")
            except (OSError, http.client.HTTPException):
                # A watcher shutdown commonly surfaces as EOF/OSError. Prefer
                # its stable reason, never the platform exception text.
                abort.check()
                _fail("network")
    finally:
        # The watcher has joined before either object can close/reuse its fd.
        if response is not None:
            try:
                response.close()
            except OSError:
                pass
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass


class YandexProvider:
    def __init__(self, config, api_key):
        if not isinstance(config, AnalysisConfig):
            _fail("configuration")
        self.config = config
        self._api_key = api_key.strip() if isinstance(api_key, str) else ""

    def __repr__(self):
        return "YandexProvider(<protected credential>)"

    def analyze(self, prepared, *, cancel=None):
        cancel = cancel if cancel is not None else Event()
        try:
            instructions, payload = prepared.instructions, prepared.payload_json
        except AttributeError:
            _fail("invalid_request")
        if not isinstance(instructions, str) or not instructions.strip() or not isinstance(payload, str):
            _fail("invalid_request")
        if len(instructions.encode("utf-8")) > 16 * 1024 or len(payload.encode("utf-8")) > 96 * 1024:
            _fail("too_large")
        try:
            parsed = json.loads(payload)
        except (ValueError, TypeError):
            _fail("invalid_request")
        if not isinstance(parsed, dict):
            _fail("invalid_request")
        return self._request(instructions, payload, self.config.max_output_tokens, cancel)

    def check_connection(self, *, cancel=None):
        """One small billable test containing no device information."""
        cancel = cancel if cancel is not None else Event()
        reply = self._request('Return JSON only: {"connection":"ok"}.',
                              '{"test":"ASIC Monitor connection check; no device data"}', 64, cancel)
        try:
            if json.loads(reply.text) != {"connection": "ok"}:
                _fail("invalid_response")
        except (ValueError, TypeError):
            _fail("invalid_response")
        return reply

    def _request(self, instructions, payload, tokens, cancel):
        _cancelled(cancel)
        if not self._api_key:
            _fail("auth_required")
        if (len(self._api_key) > 4096 or not self._api_key.isascii()
                or any(ord(char) < 33 or ord(char) == 127 for char in self._api_key)):
            _fail("auth_required")
        try:
            model = self.config.model_uri
        except ValueError:
            _fail("configuration")
        body = {"model": model, "messages": [{"role": "system", "content": instructions},
                {"role": "user", "content": payload}], "max_tokens": tokens,
                "stream": False, "store": False, "temperature": 0.2,
                "response_format": {"type": "json_object"}}
        encoded = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > MAX_REQUEST_BYTES:
            _fail("too_large")
        headers = {"Authorization": "Bearer " + self._api_key,
                   "OpenAI-Project": self.config.folder_id,
                   "x-data-logging-enabled": "false",
                   "Content-Type": "application/json", "Accept": "application/json",
                   "Accept-Encoding": "identity",
                   "User-Agent": "ASIC-Monitor-AI"}
        data = _read_response(encoded, headers, cancel, self.config.timeout_seconds)
        _cancelled(cancel)
        try:
            result = json.loads(data)
            if not isinstance(result, dict):
                raise ValueError()
            choices = result["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError()
            choice = choices[0]
            message = choice["message"]
            if choice.get("finish_reason") != "stop" or message.get("role") != "assistant":
                raise ValueError()
            if message.get("tool_calls") or message.get("function_call") or message.get("refusal"):
                raise ValueError()
            text = message["content"]
            if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
                raise ValueError()
            if self._api_key in text:
                raise ValueError()
            # The application decodes this JSON before rendering a report, so
            # also reject an echoed key hidden behind JSON Unicode escapes.
            try:
                decoded_text = json.dumps(json.loads(text), ensure_ascii=False)
            except (ValueError, TypeError, RecursionError):
                decoded_text = ""
            if self._api_key in decoded_text:
                raise ValueError()
            usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
            # Retain the configured model, not arbitrary server text containing private data.
            return ProviderReply(text=text, model=model,
                input_tokens=_token_count(usage.get("prompt_tokens")),
                output_tokens=_token_count(usage.get("completion_tokens")))
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, UnicodeError):
            _fail("invalid_response")
