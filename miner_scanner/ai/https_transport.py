"""Bounded HTTPS requests to explicitly supported AI identity and inference hosts."""
from dataclasses import dataclass, field
import http.client
import socket
import ssl
import time
from threading import Event, Lock, Thread

from .contracts import AnalysisError

TRUSTED_HOSTS = frozenset(('auth.openai.com', 'api.openai.com', 'ai.api.cloud.yandex.net'))

def _fail(code):
    raise AnalysisError(code) from None

class RequestAbort:
    """Own one request's socket and a joined cancellation/deadline watcher.

    The watcher only shuts down this socket. The request thread owns closing
    HTTP/TLS objects. OS DNS resolution runs before a socket is available and
    cannot be interrupted by this controller.
    """
    def __init__(self, cancel, seconds):
        self.cancel = cancel
        self.deadline = time.monotonic() + seconds
        self._done = Event()
        self._lock = Lock()
        self._socket = None
        self._reason = None
        self._thread = Thread(target=self._watch, name="ASICMonitorYandexAbort", daemon=False)

    def __enter__(self):
        self.check()
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._done.set()
        self._thread.join()

    @staticmethod
    def _shutdown(sock):
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _abort(self, reason):
        with self._lock:
            if self._reason is None:
                self._reason = reason
            sock = self._socket
        self._shutdown(sock)

    def attach(self, sock):
        with self._lock:
            self._socket = sock
            aborted = self._reason is not None
        if aborted:
            self._shutdown(sock)
        self.check()

    def check(self):
        with self._lock:
            reason = self._reason
        if reason is None:
            if self.cancel.is_set():
                self._abort("cancelled")
            elif time.monotonic() >= self.deadline:
                self._abort("timeout")
            with self._lock:
                reason = self._reason
        if reason is not None:
            _fail(reason)

    def remaining(self):
        self.check()
        return max(0.001, self.deadline - time.monotonic())

    def _watch(self):
        while not self._done.wait(0.05):
            if self.cancel.is_set():
                self._abort("cancelled")
                return
            if time.monotonic() >= self.deadline:
                self._abort("timeout")
                return


class FixedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, abort):
        if host not in TRUSTED_HOSTS:
            raise AnalysisError("invalid_request")
        self._fixed_host = host
        super().__init__(host, 443, timeout=min(5, abort.remaining()), context=ssl.create_default_context())
        self._abort = abort

    def connect(self):
        # OS DNS resolution cannot be interrupted. After it returns, all TCP
        # candidates share one five-second budget. Each socket is published
        # before connect; on systems that reject shutdown before connection,
        # cancellation waits only the current candidate's remaining timeout.
        addresses = socket.getaddrinfo(self._fixed_host, 443, type=socket.SOCK_STREAM)
        self._abort.check()
        tcp_deadline = min(self._abort.deadline, time.monotonic() + 5)
        last_error = OSError("No endpoint address")
        for family, kind, protocol, _, address in addresses:
            self._abort.check()
            remaining = min(self._abort.remaining(), tcp_deadline - time.monotonic())
            if remaining <= 0:
                raise TimeoutError("TCP connection deadline")
            candidate = None
            try:
                candidate = socket.socket(family, kind, protocol)
                self.sock = candidate
                self._abort.attach(candidate)
                candidate.settimeout(remaining)
                candidate.connect(address)
                self._abort.check()
                if time.monotonic() >= tcp_deadline:
                    raise TimeoutError("TCP connection deadline")
                break
            except OSError as exc:
                last_error = exc
                if candidate is not None:
                    candidate.close()
                self.sock = None
                self._abort.check()
        else:
            raise last_error
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self._fixed_host,
                                              do_handshake_on_connect=False)
        self._abort.attach(self.sock)
        self.sock.settimeout(self._abort.remaining())
        self.sock.do_handshake()
        self._abort.check()
        self.sock.settimeout(self._abort.remaining())


@dataclass(frozen=True)
class HTTPReply:
    status: int
    headers: dict
    body: bytes = field(repr=False)


def request_bytes(host, method, path, body, headers, *, cancel=None, seconds=45, max_bytes=512 * 1024):
    """One verified TLS request. No proxies, redirect following or retries."""
    if (host not in TRUSTED_HOSTS or method not in ('GET', 'POST') or not isinstance(path, str)
            or not path.startswith('/') or path.startswith('//') or '://' in path or len(path) > 4096
            or any(ord(char) < 33 or ord(char) > 126 for char in path)
            or body is not None and (not isinstance(body, bytes) or len(body) > 128 * 1024)
            or not isinstance(headers, dict) or type(max_bytes) is not int or not 1 <= max_bytes <= 2 * 1024 * 1024
            or isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 0 < seconds <= 180):
        _fail('invalid_request')
    for key, value in headers.items():
        if (not isinstance(key, str) or not isinstance(value, str) or len(key) > 100 or len(value) > 32768
                or any(ord(char) < 32 or ord(char) > 126 for char in key + value)):
            _fail('invalid_request')
    connection = response = None
    cancel = cancel if cancel is not None else Event()
    try:
        with RequestAbort(cancel, seconds) as abort:
            try:
                connection = FixedHTTPSConnection(host, abort)
                connection.request(method, path, body=body, headers={**headers, 'Accept-Encoding': 'identity'})
                abort.check()
                response = connection.getresponse()
                abort.check()
                if response.getheader('Content-Encoding', 'identity').strip().lower() != 'identity':
                    _fail('invalid_response')
                received = bytearray()
                while True:
                    abort.check()
                    chunk = response.read(min(8192, max_bytes + 1 - len(received)))
                    abort.check()
                    if not chunk:
                        break
                    received.extend(chunk)
                    if len(received) > max_bytes:
                        _fail('too_large')
                names = ('Content-Type', 'X-Request-Id', 'Cache-Control')
                safe_headers = {name.lower(): response.getheader(name, '') for name in names}
                return HTTPReply(response.status, safe_headers, bytes(received))
            except AnalysisError:
                raise
            except TimeoutError:
                abort.check()
                _fail('timeout')
            except (OSError, http.client.HTTPException):
                abort.check()
                _fail('network')
    finally:
        for resource in (response, connection):
            if resource is not None:
                try:
                    resource.close()
                except OSError:
                    pass
