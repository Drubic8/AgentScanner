import socket
import json
import struct
import time

# --- ИМПОРТ ИНТЕРФЕЙСА ---
try:
    # Вариант 1: Когда запускаем как модуль (через gemini_gui)
    from .whatsminer_interface import WhatsminerAPIv3
except ImportError:
    try:
        # Вариант 2: Если запускаем напрямую или пути настроены иначе
        from miner_scanner.handlers.whatsminer_interface import WhatsminerAPIv3
    except ImportError:
        WhatsminerAPIv3 = None
        print("Warning: WhatsminerAPIv3 library not found.")

DEFAULT_PORT = 4433
PASS_LIST = ["admin", "super", "12345678", "123456"]

class WhatsminerManager:
    def __init__(self, ip, port=DEFAULT_PORT):
        self.ip = ip
        self.port = port
        self.sock = None
        self.api_interface = None 

    def _connect(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(3)
            self.sock.connect((self.ip, self.port))
            return True
        except:
            return False

    def _close(self):
        if self.sock:
            try: self.sock.close()
            except: pass

    def _send_packet(self, data_str):
        try:
            if isinstance(data_str, dict):
                data_str = json.dumps(data_str)
                
            payload = data_str.encode('utf-8')
            header = struct.pack('<I', len(payload))
            self.sock.sendall(header + payload)
            
            head = self.sock.recv(4)
            if not head: return None
            resp_len = struct.unpack('<I', head)[0]
            
            # Защита от переполнения буфера
            if resp_len > 1000000: return None

            buffer = b""
            while len(buffer) < resp_len:
                chunk = self.sock.recv(min(4096, resp_len - len(buffer)))
                if not chunk: break
                buffer += chunk
            
            return json.loads(buffer.decode('utf-8'))
        except Exception as e:
            return {"error": str(e)}

    def _auth_and_execute(self, func_callback):
        if not WhatsminerAPIv3:
            return False, "Error: Library missing"

        last_err = ""
        
        for pwd in PASS_LIST:
            if not self._connect():
                return False, "Connection failed"
            
            try:
                # ВАЖНО: Используем 'super' для записи (как в вашем рабочем примере)
                api = WhatsminerAPIv3("super", pwd)

                req = api.get_request_cmds("get.device.info")
                resp = self._send_packet(req)
                
                if not resp or 'msg' not in resp or 'salt' not in resp['msg']:
                    last_err = "Salt error (Check API Switch)"
                    self._close()
                    continue 
                
                salt = resp['msg']['salt']
                api.set_salt(salt)

                # Выполняем переданную инструкцию
                cmd_json = func_callback(api)
                
                res = self._send_packet(cmd_json)
                self._close()

                if res and res.get('code') == 0:
                    return True, f"Success (Pwd: {pwd})"
                elif res and "password is wrong" in str(res.get('msg', '')):
                    continue 
                elif res and "invalid command" in str(res.get('msg', '')):
                    return False, f"Command not supported by FW"
                else:
                    return False, f"API Error: {res.get('msg', res)}"

            except Exception as e:
                self._close()
                last_err = str(e)

        return False, f"Auth failed: {last_err}"

    def reboot(self):
        # Используем set.system.reboot (Работает корректно)
        return self._auth_and_execute(lambda api: api.set_system_reboot())

    def blink_led(self, enable=True):
        """
        Управление светодиодом. 
        Исправлена команда на set.system.led и формат параметров.
        """
        def led_logic(api):
            if enable:
                # ВАЖНО: Параметры должны быть СПИСКОМ объектов
                # duration > 0, иначе не горит
                params = [
                    {
                        "color": "red",
                        "period": 200,    # Период 0.5 сек
                        "duration": 100,  # Горит 0.25 сек
                        "start": 0
                    },
                    {
                        "color": "green",
                        "period": 200,
                        "duration": 150,
                        "start": 0
                    }
                ]
                return api.set_request_cmds("set.system.led", params)
            else:
                # Возврат в авто-режим
                return api.set_request_cmds("set.system.led", "auto")
        
        return self._auth_and_execute(led_logic)