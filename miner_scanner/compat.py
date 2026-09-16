"""Small compatibility adapters for public protocol helpers."""
import json
from .runtime import Operation, ScanOptions, ScannerError
from .transports import Transport


class SimpleWhatsminerTCP:
    def __init__(self, ip, port=4433, timeout=10):
        self.transport = Transport(ip, Operation(ScanOptions(device_timeout=timeout, read_timeout=timeout)))
        self.port = port
        self.sock = None
        self._connection = None

    def connect(self):
        try:
            self._connection = self.transport.connection(self.port)
            self.sock = self._connection.__enter__()
            return True
        except (OSError, ScannerError):
            self.sock = None
            return False

    def close(self):
        if self.sock is not None:
            self._connection.__exit__(None, None, None)
            self.sock = None
        self.transport.close()

    def send_cmd(self, cmd, param=None):
        if self.sock is None:
            return None
        payload = {"cmd": cmd}
        if param is not None:
            payload["param"] = param
        try:
            return self.transport.rpc_packet(self.sock, payload)
        except (OSError, ScannerError):
            return None


class WhatsminerTCP(SimpleWhatsminerTCP):
    def __init__(self, ip, port, account, password):
        super().__init__(ip, port)
        self.account = account
        self.password = password

    def send(self, message, message_length=None):
        # Historical callers pass character length; framing uses UTF-8 byte length.
        return self.transport.rpc_packet(self.sock, json.loads(message))
