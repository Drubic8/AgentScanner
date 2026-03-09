import socket
import json

TARGET_IP = "192.168.154.183"

# Список портов для проверки (SSH, Web, HTTPS, API майнеров)
PORTS_TO_CHECK = [22, 80, 443, 4028, 4029, 8889, 3333, 14223, 3956]
COMMANDS = ["summary", "stats", "pools"]

def scan_ports(ip):
    print(f"🔍 Сканируем открытые порты на {ip}...")
    open_ports = []
    for port in PORTS_TO_CHECK:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5) # Ждем полсекунды
                result = s.connect_ex((ip, port))
                if result == 0:
                    print(f"  [++] Порт {port} ОТКРЫТ!")
                    open_ports.append(port)
                else:
                    print(f"  [--] Порт {port} закрыт (Код: {result})")
        except Exception as e:
            pass
    return open_ports

def send_api_command(ip, port, cmd):
    print(f"\n📨 Отправляем команду '{cmd}' на порт {port}...")
    
    # Формируем стандартную JSON команду (как в Antminer)
    payload = json.dumps({"command": cmd})
    
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(3.0)
            s.connect((ip, port))
            s.sendall(payload.encode('utf-8'))
            
            # Получаем ответ
            data = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
            
            response = data.decode('utf-8', errors='ignore')
            print(f"  [✓] Ответ получен ({len(data)} байт):")
            # Выводим первые 500 символов, чтобы не засорять консоль
            print("  " + response[:500] + ("..." if len(response) > 500 else ""))
            
    except ConnectionRefusedError:
        print("  [❌] Ошибка: [WinError 10061] Подключение отвергнуто (Порт закрыт или API отключено в прошивке)")
    except Exception as e:
        print(f"  [❌] Ошибка: {e}")

if __name__ == "__main__":
    # 1. Сначала узнаем открытые порты
    open_ports = scan_ports(TARGET_IP)
    
    # 2. Если есть 4028, пробуем отправить туда команды
    if 4028 in open_ports:
        print("\n🚀 Порт 4028 открыт! Пробуем достучаться до API...")
        for cmd in COMMANDS:
            send_api_command(TARGET_IP, 4028, cmd)
    else:
        print("\n⚠️ Стандартный порт API (4028) ЗАКРЫТ.")
        print("Это значит, что прошивка жестко блокирует запросы извне. В этом случае работает ТОЛЬКО парсинг Web-страницы (80 порт), который мы сделали до этого.")
        
        # Если открыт какой-то нестандартный порт (например 4029), попробуем его
        other_api_ports = [p for p in open_ports if p not in [22, 80, 443]]
        if other_api_ports:
            test_port = other_api_ports[0]
            print(f"\n🤔 Заметил открытый порт {test_port}. Пробую отправить команду туда...")
            send_api_command(TARGET_IP, test_port, "summary")