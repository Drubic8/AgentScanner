import socket
import json

IP = "192.168.154.107"  # Укажите IP спящего Antminer
PORT = 4028

def send_cmd(ip, cmd):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect((ip, PORT))
        
        # Отправляем команду
        s.sendall(json.dumps({"command": cmd}).encode('utf-8'))
        
        # Читаем ответ
        full_resp = b""
        while True:
            chunk = s.recv(4096)
            if not chunk: break
            full_resp += chunk
        s.close()
        
        clean = full_resp.decode('utf-8', errors='ignore').replace('\x00', '').strip()
        if not clean:
            return "ПУСТОЙ ОТВЕТ"
            
        return json.loads(clean)
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    print(f"=== ПОЛНЫЙ ДАМП API ANTMINER (Спящий режим) IP: {IP} ===")
    
    # Расширенный список команд API cgminer/bmminer
    commands = [
        "summary", 
        "stats", 
        "estats",     # Extended stats (Расширенная статистика)
        "pools", 
        "devs",       # Devices (Устройства)
        "edevs",      # Extended devices 
        "devdetails", # Детальная инфа по чипам
        "config",     # Конфигурация
        "version",    # Версия
        "check"       # Проверка статуса (на некоторых прошивках)
    ]
    
    for cmd in commands:
        print(f"\n{'='*60}")
        print(f"--- КОМАНДА: {cmd.upper()} ---")
        resp = send_cmd(IP, cmd)
        
        if isinstance(resp, dict):
            # Если ответ слишком огромный (например, devdetails может выдать тысячи строк), 
            # мы его немного укоротим, чтобы консоль не зависла, но суть оставим.
            dump_str = json.dumps(resp, indent=2)
            if len(dump_str) > 3000:
                print(dump_str[:1500] + "\n\n... [ОТВЕТ СЛИШКОМ БОЛЬШОЙ, ПОКАЗАНА ТОЛЬКО ЧАСТЬ] ...\n\n" + dump_str[-1500:])
            else:
                print(dump_str)
        else:
            print(resp)