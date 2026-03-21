import requests
from requests.auth import HTTPDigestAuth
import socket
import json
import struct
import time

# --- ИМПОРТ ИНТЕРФЕЙСА ДЛЯ WHATSMINER ---
try:
    from .whatsminer_interface import WhatsminerAPIv3
except ImportError:
    try:
        from miner_scanner.handlers.whatsminer_interface import WhatsminerAPIv3
    except ImportError:
        WhatsminerAPIv3 = None


def send_command(ip, make, action):
    """
    Универсальный диспетчер команд для ASIC-майнеров.
    action: 'led_on', 'led_off', 'reboot', 'sleep', 'normal'
    """
    make = str(make).lower()
    
    try:
        if "elphapex" in make:
            return _cmd_elphapex(ip, action)
        elif "jasminer" in make:
            return _cmd_jasminer(ip, action)
        elif "whatsminer" in make or "microbt" in make:
            return _cmd_whatsminer(ip, action)
        elif "bitmain" in make or "antminer" in make or "vnish" in make:
            return _cmd_antminer(ip, action)
        else:
            return False, f"Управление для производителя '{make}' пока не поддерживается"
    except requests.exceptions.Timeout:
        return False, "Устройство не ответило (Таймаут)"
    except Exception as e:
        return False, f"Ошибка сети: {str(e)}"


# =====================================================================
# ELPHAPEX (Прямое управление через /luci/ без пароля)
# =====================================================================
def _cmd_elphapex(ip, action):
    headers = {'Content-Type': 'application/json'}
    
    if action == "led_on":
        url = f"http://{ip}/cgi-bin/luci/ftm_ledtest.cgi"
        payload = {"leds_blue": 1, "leds_red": 0, "leds_flash": 1, "leds_time": 0}
    elif action == "led_off":
        url = f"http://{ip}/cgi-bin/luci/ftm_ledtest.cgi"
        payload = {"leds_blue": 0, "leds_red": 0, "leds_flash": 0, "leds_time": 0}
    elif action == "sleep":
        url = f"http://{ip}/cgi-bin/luci/setworkmode.cgi"
        payload = {"workmode": "-1000"}
    elif action == "normal":
        url = f"http://{ip}/cgi-bin/luci/setworkmode.cgi"
        payload = {"workmode": "0"}
    elif action == "reboot":
        url = f"http://{ip}/cgi-bin/luci/reboot.cgi"
        payload = {}
    else:
        return False, "Команда не поддерживается для Elphapex"

    resp = requests.post(url, json=payload, headers=headers, timeout=3)
    if resp.status_code == 200:
        return True, f"Команда {action} успешно отправлена"
    return False, f"Ошибка Elphapex HTTP {resp.status_code}"


# =====================================================================
# JASMINER (Управление через Digest Auth)
# =====================================================================
def _cmd_jasminer(ip, action):
    auth = HTTPDigestAuth("root", "root")
    
    if action == "led_on":
        url = f"http://{ip}/cgi-bin/find_miner_on.cgi"
        resp = requests.post(url, auth=auth, timeout=3)
        if resp.status_code == 200 and "ok" in resp.text.lower():
            return True, "LED (Поиск) включен"
        return False, f"Ошибка устройства ({resp.status_code})"
        
    elif action == "led_off":
        url = f"http://{ip}/cgi-bin/find_miner_off.cgi"
        resp = requests.post(url, auth=auth, timeout=3)
        if resp.status_code == 200:
            return True, "LED выключен"
        return False, f"Ошибка устройства ({resp.status_code})"
        
    else:
        return False, f"Команда '{action}' пока не поддерживается для JasMiner"


def _cmd_antminer(ip, action):
    auth_fallback = HTTPDigestAuth("root", "root")
    
    # =================================================================
    # 1. Автодетект: Проверяем, стоит ли VNish (по официальному API v1)
    # =================================================================
    try:
        test_req = requests.get(f"http://{ip}/api/v1/info", timeout=1.5)
        is_vnish = (test_req.status_code == 200)
    except:
        is_vnish = False

    if is_vnish:
        # === ЛОГИКА VNISH (JWT ТОКЕН) ===
        token = None
        try:
            token_req = requests.post(f"http://{ip}/api/v1/unlock", json={"pw": "root"}, timeout=3)
            if token_req.status_code == 200:
                token = token_req.json().get("token")
        except:
            pass

        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = f"Bearer {token}"
            
        auth_method = None if token else auth_fallback

        try:
            if action == "led_on":
                url = f"http://{ip}/api/v1/find-miner"
                payload = {"on": True}
                resp = requests.post(url, json=payload, headers=headers, auth=auth_method, timeout=3)
            elif action == "led_off":
                url = f"http://{ip}/api/v1/find-miner"
                payload = {"on": False}
                resp = requests.post(url, json=payload, headers=headers, auth=auth_method, timeout=3)
            elif action == "reboot":
                url = f"http://{ip}/api/v1/system/reboot"
                resp = requests.post(url, headers=headers, auth=auth_method, timeout=3)
            elif action == "sleep":
                url = f"http://{ip}/api/v1/mining/stop"
                resp = requests.post(url, headers=headers, auth=auth_method, timeout=3)
            elif action == "normal":
                url = f"http://{ip}/api/v1/mining/start"
                resp = requests.post(url, headers=headers, auth=auth_method, timeout=3)
            else:
                return False, "Команда не поддерживается для VNish"
                
            if resp.status_code in [200, 204]:
                return True, "Успешно (VNish)"
                
            # Костыль для очень старых версий VNish
            if resp.status_code == 404 and "led" in action:
                old_url = f"http://{ip}/api/v1/system/blink"
                old_payload = {"blink": (action == "led_on")}
                fallback_resp = requests.post(old_url, json=old_payload, headers=headers, auth=auth_method, timeout=3)
                if fallback_resp.status_code in [200, 204]:
                    return True, "Успешно (Legacy VNish)"
                    
            return False, f"Ошибка VNish: HTTP {resp.status_code} - {resp.text[:30]}"
        except Exception as e:
            return False, f"Сбой VNish: {str(e)}"
            
    else:
        # =================================================================
        # 2. Логика для заводской прошивки (Antminer Stock из Wireshark)
        # =================================================================
        try:
            if action == "reboot":
                url = f"http://{ip}/cgi-bin/reboot.cgi"
                resp = requests.get(url, auth=auth_fallback, timeout=10)
                if resp.status_code == 200:
                    return True, "Команда перезагрузки отправлена (Stock)"
                return False, f"Ошибка сети: HTTP {resp.status_code}"

            elif action in ["led_on", "led_off"]:
                url = f"http://{ip}/cgi-bin/blink.cgi"
                # Отправляем именно JSON, как требует свежая прошивка
                payload = {"blink": True if action == "led_on" else False}
                
                resp = requests.post(url, auth=auth_fallback, json=payload, timeout=10)
                
                # Асик отвечает {"code":"B000"} при успехе
                if resp.status_code == 200:
                    return True, f"Подсветка {'включена' if action == 'led_on' else 'выключена'} (Stock)"
                return False, f"Ошибка API: HTTP {resp.status_code}"

            elif action in ["sleep", "normal"]:
                # 1. Скачиваем текущий конфиг
                get_url = f"http://{ip}/cgi-bin/get_miner_conf.cgi"
                conf_resp = requests.get(get_url, auth=auth_fallback, timeout=10)
                if conf_resp.status_code != 200:
                    return False, "Не удалось получить текущий конфиг устройства"
                
                config = conf_resp.json()
                
                # 2. Меняем режим работы (1 - Sleep, 0 - Normal)
                config["bitmain-work-mode"] = "1" if action == "sleep" else "0"
                
                # 3. Отправляем обновленный конфиг обратно
                set_url = f"http://{ip}/cgi-bin/set_miner_conf.cgi"
                resp = requests.post(set_url, auth=auth_fallback, json=config, timeout=15)
                
                if resp.status_code == 200:
                    return True, f"Режим {'Сон' if action == 'sleep' else 'Работа'} успешно применен (Stock)"
                return False, f"Ошибка сохранения конфига: {resp.status_code}"

            return False, "Команда не поддерживается"
        except Exception as e:
            return False, f"Сбой Stock Antminer: {str(e)}"


# =====================================================================
# WHATSMINER (MicroBT) - ТВОЙ ОРИГИНАЛЬНЫЙ КОД
# =====================================================================
DEFAULT_PORT = 4433
PASS_LIST = ["admin", "super", "12345678", "123456"]

class WhatsminerManager:
    def __init__(self, ip, port=DEFAULT_PORT):
        self.ip = ip
        self.port = port
        self.sock = None

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
            return False, "Error: Library WhatsminerAPIv3 missing"

        last_err = ""
        for pwd in PASS_LIST:
            if not self._connect():
                return False, "Connection failed"
            
            try:
                api = WhatsminerAPIv3("super", pwd)
                req = api.get_request_cmds("get.device.info")
                resp = self._send_packet(req)
                
                if not resp or 'msg' not in resp or 'salt' not in resp['msg']:
                    last_err = "Salt error"
                    self._close()
                    continue 
                
                salt = resp['msg']['salt']
                api.set_salt(salt)

                cmd_json = func_callback(api)
                res = self._send_packet(cmd_json)
                self._close()

                if res and res.get('code') == 0:
                    return True, f"Success (Pwd: {pwd})"
                elif res and "password is wrong" in str(res.get('msg', '')):
                    continue 
                elif res and "invalid command" in str(res.get('msg', '')):
                    return False, "Command not supported by FW"
                else:
                    return False, f"API Error: {res.get('msg', res)}"

            except Exception as e:
                self._close()
                last_err = str(e)

        return False, f"Auth failed: {last_err}"

    def reboot(self):
        return self._auth_and_execute(lambda api: api.set_request_cmds("set.system.reboot", None))

    def blink_led(self, enable=True):
        def led_logic(api):
            if enable:
                params = [
                    {"color": "red", "period": 200, "duration": 100, "start": 0},
                    {"color": "green", "period": 200, "duration": 150, "start": 0}
                ]
                return api.set_request_cmds("set.system.led", params)
            else:
                return api.set_request_cmds("set.system.led", "auto")
        return self._auth_and_execute(led_logic)
        
    def set_mining_state(self, state):
        """
        Управление службой майнинга (Сон/Работа).
        state: "stop" (Сон/Suspend) или "start" (Работа/Resume)
        """
        def state_logic(api):
            # Передаем строку напрямую в param, как описано в документации Whatsminer
            return api.set_request_cmds("set.miner.service", state)
        return self._auth_and_execute(state_logic)


# =====================================================================
# Маршрутизатор для Whatsminer
# =====================================================================
def _cmd_whatsminer(ip, action):
    wm = WhatsminerManager(ip)
    
    if action == "reboot":
        return wm.reboot()
    elif action == "led_on":
        return wm.blink_led(True)
    elif action == "led_off":
        return wm.blink_led(False)
    elif action == "sleep":
        # Останавливаем демона btminer (аналог Suspend)
        return wm.set_mining_state("stop")
    elif action == "normal":
        # Запускаем демона btminer (аналог Resume)
        return wm.set_mining_state("start")
    else:
        return False, f"Команда {action} неизвестна"