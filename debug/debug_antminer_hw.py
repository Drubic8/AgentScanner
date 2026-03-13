import socket
import json
import requests
from requests.auth import HTTPDigestAuth

IP = "192.168.59.40"
PORT = 4028
USER = "root"
PWD = "root"

def send_cmd(cmd):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect((IP, PORT))
        s.sendall(json.dumps({"command": cmd}).encode('utf-8'))
        full_resp = b""
        while True:
            chunk = s.recv(4096)
            if not chunk: break
            full_resp += chunk
        s.close()
        clean = full_resp.decode('utf-8', errors='ignore').replace('\x00', '').strip()
        return json.loads(clean)
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    print(f"=== ДЕТАЛЬНЫЙ АНАЛИЗ ОШИБКИ ПЛАТ ANTMINER {IP} ===\n")
    
    # 1. Запрос по порту 4028 (ищем крестики)
    stats = send_cmd("stats")
    print("--- СОСТОЯНИЕ ХЕШ-ПЛАТ (Порт 4028) ---")
    
    if "STATS" in stats and len(stats["STATS"]) > 1:
        st = stats["STATS"][1]
        for i in range(1, 9):
            acs_key = f"chain_acs{i}"
            if acs_key in st:
                val = str(st[acs_key])
                if val:
                    # Подсвечиваем платы с ошибками
                    if 'x' in val.lower():
                        print(f"Плата {i} [{acs_key}]: СБОЙ! -> {val}")
                    else:
                        print(f"Плата {i} [{acs_key}]: ОК -> {val[:20]}... (и т.д.)")
    else:
        print("Не удалось получить STATS.")

    # 2. Запрос по порту 80 (ищем системные ошибки)
    print("\n--- СОСТОЯНИЕ ВЕБ-ИНТЕРФЕЙСА (Порт 80) ---")
    try:
        resp = requests.get(f"http://{IP}/cgi-bin/summary.cgi", auth=HTTPDigestAuth(USER, PWD), timeout=3)
        if resp.status_code == 200:
            status_array = resp.json().get("SUMMARY", [{}])[0].get("status", [])
            found_web_error = False
            for item in status_array:
                if str(item.get("status")).lower() != "s":
                    print(f"ОШИБКА: [{item.get('type')}] {item.get('msg')}")
                    found_web_error = True
            if not found_web_error:
                print("Веб-интерфейс не сообщает об ошибках (статусы 's').")
        else:
            print(f"Код {resp.status_code}. Возможно, изменен пароль или нет веб-интерфейса.")
    except Exception as e:
        print(f"Не удалось подключиться к порту 80: {e}")