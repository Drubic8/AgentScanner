"""Runs only in instrumentation tests against loopback, never a user's LAN."""
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
from threading import Thread

from requests.utils import parse_dict_header
from miner_scanner.models import Credentials
from miner_scanner.runtime import AuthenticationError, Operation, ScanOptions
from miner_scanner.transports import Transport


def run_protocol_smoke():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.listen(1)

    def reply():
        connection, _ = server.accept()
        with connection:
            connection.recv(4096)
            connection.sendall(b'{"STATUS":[{"STATUS":"S"}]}\x00')
        server.close()

    thread = Thread(target=reply, daemon=True)
    thread.start()
    with Transport("127.0.0.1", Operation(ScanOptions())) as transport:
        assert transport.cgminer("version", port=port)["STATUS"][0]["STATUS"] == "S"
    thread.join(3)
    assert not thread.is_alive()

    class AuthHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            header = self.headers.get("Authorization", "")
            valid = False
            if self.path == "/basic":
                valid = header == "Basic " + base64.b64encode(b"tester:synthetic-password").decode()
                challenge = 'Basic realm="test"'
            else:
                challenge = 'Digest realm="test", nonce="synthetic-nonce", algorithm=MD5, qop="auth"'
                if header.startswith("Digest "):
                    data = parse_dict_header(header[7:])
                    def digest(text):
                        return hashlib.md5(text.encode()).hexdigest()
                    ha1 = digest("tester:test:synthetic-password")
                    ha2 = digest("GET:/digest")
                    expected = digest(f'{ha1}:synthetic-nonce:{data.get("nc")}:{data.get("cnonce")}:auth:{ha2}')
                    valid = data.get("username") == "tester" and data.get("uri") == "/digest" and data.get("response") == expected
            if not valid:
                self.send_response(401)
                self.send_header("WWW-Authenticate", challenge)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            payload = json.dumps({"model": "synthetic-ASIC", "authenticated": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    http = ThreadingHTTPServer(("127.0.0.1", 0), AuthHandler)
    thread = Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        for auth in ("basic", "digest"):
            with Transport("127.0.0.1", Operation(ScanOptions()), Credentials("tester", "synthetic-password", auth)) as transport:
                result = transport.http_json("/" + auth, port=http.server_port)
                assert result["authenticated"] is True
            with Transport("127.0.0.1", Operation(ScanOptions()), Credentials("tester", "wrong-password", auth)) as transport:
                try:
                    transport.http_json("/" + auth, port=http.server_port)
                except AuthenticationError:
                    pass
                else:
                    raise AssertionError("Wrong password accepted")
    finally:
        http.shutdown()
        http.server_close()
        thread.join(3)


run_protocol_smoke()
