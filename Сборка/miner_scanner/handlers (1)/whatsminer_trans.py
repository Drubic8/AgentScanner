import socket
import hashlib
import base64
import json
import time
import struct

class WhatsminerTCP:
    def __init__(self, ip, port, account, password):
        self.ip = ip
        self.port = port
        self.account = account
        self.password = password

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((self.ip, self.port))

    def close(self):
        self.sock.shutdown(socket.SHUT_RDWR)
        self.sock.close()

    def send(self, message, message_length):
        length_bytes = struct.pack('<I', message_length)
        self.sock.sendall(length_bytes)
        self.sock.sendall(message.encode())
        response = self._receive_response()
        return json.loads(response)  

    def _receive_response(self):
        """Receive the response from the TCP connection"""
        # self.sock.setblocking(True)
        buffer = b""
        #
        # read the first 4 bytes for response json length. 
        #
        length_data = self.sock.recv(4)
        if len(length_data) < 4:
            print("Failed to receive the full length information")
            return None
        rsp_len = struct.unpack('<I', length_data)
        #print("Expected message length:", rsp_len[0])
        if rsp_len[0] > 8192:
            print("invalid rsp length:", length_data)
            return None
        while len(buffer) < rsp_len[0]:
            #print("trying to get data")
            more_data = self.sock.recv(rsp_len[0] - len(buffer))
            if not more_data:
                break
            buffer += more_data
        response = buffer.decode()
        return response              