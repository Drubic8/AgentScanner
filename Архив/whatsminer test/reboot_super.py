import socket
import json
import struct
import hashlib
import base64
import time
import sys

# === НАСТРОЙКИ ===
TARGET_IP = "10.10.202.237"
PORT = 4433  # Plain TCP

# ПАРОЛИ ДЛЯ ПРОВЕРКИ
# Учетная запись будет 'super', но пароль может быть 'admin'
PASSWORDS = ["admin", "super", "12345678", "123456"]

class WhatsminerSimple:
    def __init__(self, ip, port):
        self.ip = ip
        self.port = port
        self.sock = None

    def connect(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(5)
            self.sock.connect((self.ip, self.port))
            return True
        except Exception as e:
            print(f"❌ Connect Error: {e}")
            return False

    def close(self):
        if self.sock:
            try: self.sock.close()
            except: pass

    def send_packet(self, data_dict):
        try:
            json_str = json.dumps(data_dict, separators=(',', ':'))
            payload = json_str.encode('utf-8')
            header = struct.pack('<I', len(payload))
            self.sock.sendall(header + payload)
            
            head = self.sock.recv(4)
            if not head: return None
            resp_len = struct.unpack('<I', head)[0]
            
            buffer = b""
            while len(buffer) < resp_len:
                chunk = self.sock.recv(min(4096, resp_len - len(buffer)))
                if not chunk: break
                buffer += chunk
            return json.loads(buffer.decode('utf-8'))
        except: return None

def generate_token(cmd, password, salt, ts):
    # Токен генерируется так: cmd + password + salt + ts
    raw_str = f"{cmd}{password}{salt}{ts}"
    sha = hashlib.sha256(raw_str.encode('utf-8')).digest()
    b64 = base64.b64encode(sha).decode('utf-8')
    return b64[:8]

def try_reboot(ip, port, password):
    print(f"🔑 Пробуем Account: 'super' | Password: '{password}'...")
    
    client = WhatsminerSimple(ip, port)
    if not client.connect(): return False

    try:
        # 1. Запрос Salt
        req = {"cmd": "get.device.info"}
        resp = client.send_packet(req)
        
        if not resp or 'msg' not in resp:
            print(f"   ⚠️ Ошибка Info: {resp}")
            return False

        salt = resp['msg'].get('salt')
        # print(f"   ✅ Salt: {salt}")

        # 2. Команда
        cmd_name = "set.system.reboot"
        ts = int(time.time())
        token = generate_token(cmd_name, password, salt, ts)
        
        # ВАЖНО: Account = 'super' (согласно документации)
        payload = {
            "cmd": cmd_name,
            "ts": ts,
            "token": token,
            "account": "super" 
        }
        
        resp_reboot = client.send_packet(payload)
        
        if resp_reboot and resp_reboot.get('code') == 0:
            print(f"🚀 УСПЕХ! Асик перезагружается.")
            return True
        else:
            print(f"   ❌ Отказ: {resp_reboot}")
            return False

    finally:
        client.close()

if __name__ == "__main__":
    print("--- Start Reboot Sequence ---")
    for pwd in PASSWORDS:
        if try_reboot(TARGET_IP, PORT, pwd):
            break
    else:
        print("💀 Не удалось подобрать пароль для пользователя 'super'.")
        print("Совет: Измените пароль 'admin' на веб-морде, это разблокирует API.")