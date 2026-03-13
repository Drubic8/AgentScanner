import socket
import json

IP = "192.168.135.246" # IP сломанного Antminer T21
PORT = 4028

def send_cmd(ip, cmd):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect((ip, PORT))
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
    print(f"=== ГЛУБОКИЙ АНАЛИЗ ОШИБКИ ANTMINER {IP} ===")
    
    # Отправляем три главные команды и смотрим сырой ответ
    commands = ["summary", "stats", "pools"]
    
    for cmd in commands:
        print(f"\n{'='*50}")
        print(f"--- ОТВЕТ НА КОМАНДУ: {cmd.upper()} ---")
        resp = send_cmd(IP, cmd)
        
        # Выводим полный JSON, чтобы не упустить ни одной скрытой строчки
        if "error" in resp:
            print(f"Ошибка подключения: {resp['error']}")
        else:
            print(json.dumps(resp, indent=2))