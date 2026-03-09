import socket
import json
import struct
import ssl  # <--- Добавили библиотеку SSL

class WhatsminerTCP:
    def __init__(self, ip, port, account, password):
        self.ip = ip
        self.port = port
        self.account = account
        self.password = password
        self.sock = None

    def connect(self):
        # Создаем базовый сокет
        raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw_sock.settimeout(10) # Увеличили таймаут
        
        # Если порт 4433 - оборачиваем в SSL
        if self.port == 4433:
            print(f"🔒 Включение SSL режима для порта {self.port}...")
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            self.sock = context.wrap_socket(raw_sock, server_hostname=self.ip)
        else:
            # Иначе (например порт 8889 без SSL) - оставляем как есть
            self.sock = raw_sock

        self.sock.connect((self.ip, self.port))

    def close(self):
        if self.sock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
                self.sock.close()
            except:
                pass

    def send(self, message, message_length):
        # Формируем пакет согласно протоколу: [Длина 4 байта] + [JSON данные]
        length_bytes = struct.pack('<I', message_length)
        
        # message может прийти как словарь (dict), так и готовая строка JSON
        if isinstance(message, dict):
            msg_bytes = json.dumps(message).encode('utf-8')
        else:
            msg_bytes = message.encode('utf-8')

        # Отправляем
        self.sock.sendall(length_bytes)
        self.sock.sendall(msg_bytes)
        
        # Ждем ответ
        response = self._receive_response()
        
        # Декодируем ответ в JSON
        if response:
            try:
                return json.loads(response)
            except:
                return {"code": -1, "msg": "Invalid JSON response", "raw": str(response)}
        return {"code": -1, "msg": "No response"}

    def _receive_response(self):
        """Получение ответа от TCP соединения"""
        buffer = b""
        
        # 1. Читаем заголовок (4 байта длины)
        length_data = self.sock.recv(4)
        if len(length_data) < 4:
            print("Failed to receive the full length information")
            return None
            
        # Распаковываем длину (Little Endian Unsigned Int)
        rsp_len = struct.unpack('<I', length_data)[0]
        
        # 2. Читаем тело сообщения
        while len(buffer) < rsp_len:
            chunk = self.sock.recv(min(4096, rsp_len - len(buffer)))
            if not chunk:
                break
            buffer += chunk
            
        return buffer.decode('utf-8')