import json
import socket
import struct
import time

IP = "192.168.90.25"  # Укажите IP рабочего WhatsMiner
PORT = 4433

def send_cmd(ip, port, cmd, param=None):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect((ip, port))
        
        payload = {"cmd": cmd}
        if param: payload["param"] = param
            
        json_str = json.dumps(payload)
        pkg = struct.pack('<I', len(json_str)) + json_str.encode('utf-8')
        s.sendall(pkg)
        
        len_data = s.recv(4)
        if not len_data or len(len_data) < 4: return None
        pkg_len = struct.unpack('<I', len_data)[0]
        
        chunks = []
        bytes_recd = 0
        while bytes_recd < pkg_len:
            chunk = s.recv(min(pkg_len - bytes_recd, 4096))
            if not chunk: break
            chunks.append(chunk)
            bytes_recd += len(chunk)
            
        raw_resp = b''.join(chunks).decode('utf-8', errors='ignore')
        return json.loads(raw_resp)
    except Exception as e:
        return {"error": str(e)}
    finally:
        try: s.close()
        except: pass

def check_status():
    print("\n--- ЗАПРОС ТЕКУЩЕГО СТАТУСА ---")
    info = send_cmd(IP, PORT, "get.device.info")
    if info and "msg" in info and isinstance(info["msg"], dict):
        miner_info = info["msg"].get("miner", {})
        working = miner_info.get("working", "unknown")
        print(f"Поле 'working': {working}")
        
    summary = send_cmd(IP, PORT, "get.miner.status", "summary")
    if summary and "msg" in summary:
        print(f"Ответ summary: {str(summary)[:200]}...")

if __name__ == "__main__":
    print(f"=== ТЕСТ СТАТУСОВ WHATSMINER {IP} ===")
    
    # 1. Проверяем текущий статус (должен быть working: true)
    check_status()
    
    print("\n[ВНИМАНИЕ] Сейчас мы отправим команду SLEEP (power_off)")
    print("Асик должен остановить майнинг и сбросить обороты кулеров.")
    time.sleep(3)
    
    # 2. Отправляем в СОН
    resp_sleep = send_cmd(IP, PORT, "power_off")
    print(f"Ответ на power_off: {resp_sleep}")
    
    print("\nЖдем 10 секунд, чтобы асик уснул...")
    time.sleep(10)
    
    # 3. Проверяем статус во сне (должен быть working: false)
    check_status()
    
    print("\n[ВНИМАНИЕ] Возвращаем асик к работе (power_on)")
    time.sleep(3)
    
    # 4. БУДИМ АСИК
    resp_wake = send_cmd(IP, PORT, "power_on")
    print(f"Ответ на power_on: {resp_wake}")
    
    print("\nЖдем 5 секунд...")
    time.sleep(5)
    
    # 5. Проверяем статус после пробуждения (должен вернуться в working: true)
    check_status()
    print("\n=== ТЕСТ ЗАВЕРШЕН ===")