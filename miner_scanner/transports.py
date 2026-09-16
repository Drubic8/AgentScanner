"""Bounded HTTP / JSON TCP transports; no manufacturer detection here."""
import json
import socket
import struct
from contextlib import contextmanager

import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

from .models import Credentials
from .runtime import AuthenticationError, Operation, ProtocolError


def recv_exact(sock, length: int, operation: Operation) -> bytes:
    if not 0 <= length <= operation.options.max_response_bytes:
        raise ProtocolError("Invalid TCP frame length")
    result = bytearray()
    while len(result) < length:
        sock.settimeout(operation.timeout())
        chunk = sock.recv(min(length - len(result), 8192))
        if not chunk:
            raise ProtocolError("Incomplete TCP frame")
        result.extend(chunk)
    return bytes(result)


class Transport:
    def __init__(self, ip: str, operation: Operation, credentials: Credentials | None = None):
        self.ip = ip
        self.operation = operation
        self.session = requests.Session()
        self.session.trust_env = False  # LAN addresses must not inherit system HTTP proxies.
        if credentials:
            auth_class = {"digest": HTTPDigestAuth, "basic": HTTPBasicAuth}.get(credentials.auth)
            if auth_class is None:
                raise ValueError("Unknown HTTP authentication scheme")
            self.session.auth = auth_class(credentials.username, credentials.password)

    def close(self):
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @contextmanager
    def connection(self, port: int):
        op = self.operation
        sock = socket.create_connection((self.ip, port), timeout=op.timeout(op.options.connect_timeout))
        try:
            yield sock
        finally:
            sock.close()

    def http(self, path: str, method: str = "GET", *, payload=None, headers=None, port=80):
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("Expected a local absolute API path")
        op = self.operation
        op.request_count += 1
        scheme = "https" if port == 443 else "http"
        with self.session.request(
            method, f"{scheme}://{self.ip}:{port}{path}", json=payload,
            headers=headers, timeout=(op.timeout(op.options.connect_timeout), op.timeout()),
            stream=True, allow_redirects=False,
        ) as response:
            if response.status_code in (401, 403):
                raise AuthenticationError("Device credentials or write access required")
            content = bytearray()
            # read1 yields available bytes instead of waiting to fill a chunk.
            # This allows a deadline check even against a trickling HTTP peer.
            while True:
                op.remaining()
                chunk = response.raw.read1(4096, decode_content=True)
                if not chunk:
                    break
                content.extend(chunk)
                if len(content) > op.options.max_response_bytes:
                    raise ProtocolError("HTTP response too large")
            op.remaining()
            return response.status_code, bytes(content)

    def http_json(self, path: str, method="GET", **kwargs):
        status, raw = self.http(path, method, **kwargs)
        if status != 200:
            return None
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise ProtocolError("Expected a JSON API response") from exc
        if not isinstance(value, dict):
            raise ProtocolError("Expected a JSON object")
        return value

    def cgminer(self, command: str, *, port=4028, raw=False, repair=None):
        op = self.operation
        op.request_count += 1
        payload = command.encode() if raw else json.dumps({"command": command, "parameter": ""}).encode()
        with self.connection(port) as sock:
            sock.settimeout(op.timeout())
            sock.sendall(payload)
            data = bytearray()
            while True:
                sock.settimeout(op.timeout())
                chunk = sock.recv(min(8192, op.options.max_response_bytes + 1 - len(data)))
                if chunk:
                    data.extend(chunk)
                if len(data) > op.options.max_response_bytes:
                    raise ProtocolError("CGMiner response too large")
                complete = not chunk or b"\0" in data
                raw_data = bytes(data).split(b"\0", 1)[0]
                try:
                    value = json.loads(raw_data)
                    if not isinstance(value, dict):
                        raise ProtocolError("Expected a CGMiner JSON object")
                    # A nested closing brace alone cannot terminate a response.
                    return value
                except (ValueError, UnicodeError):
                    if complete:
                        if repair is not None:
                            return json.loads(repair(raw_data.decode("utf-8")))
                        raise ProtocolError("Invalid CGMiner JSON response")

    def rpc(self, command: str, parameter=None, *, port=4433):
        payload = {"cmd": command}
        if parameter is not None:
            payload["param"] = parameter
        with self.connection(port) as sock:
            return self.rpc_packet(sock, payload)

    def text_command(self, command, *, port=4028):
        op = self.operation
        op.request_count += 1
        with self.connection(port) as sock:
            sock.settimeout(op.timeout())
            sock.sendall(command.encode("utf-8"))
            response = bytearray()
            while True:
                sock.settimeout(op.timeout())
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response.extend(chunk)
                if len(response) > op.options.max_response_bytes:
                    raise ProtocolError("Text response too large")
                if b"\0" in response:
                    break
            return response.split(b"\0", 1)[0].decode("utf-8")

    def rpc_packet(self, sock, payload):
        op = self.operation
        op.request_count += 1
        encoded = payload.encode("utf-8") if isinstance(payload, str) else json.dumps(payload).encode("utf-8")
        if len(encoded) > op.options.max_response_bytes:
            raise ProtocolError("RPC request too large")
        sock.settimeout(op.timeout())
        sock.sendall(struct.pack("<I", len(encoded)) + encoded)
        length = struct.unpack("<I", recv_exact(sock, 4, op))[0]
        try:
            value = json.loads(recv_exact(sock, length, op))
        except (ValueError, UnicodeError) as exc:
            raise ProtocolError("Invalid RPC JSON response") from exc
        if not isinstance(value, dict):
            raise ProtocolError("Expected an RPC object")
        return value
