import socket
import json
import time

# Укажите IP-адрес вашего Antminer
IP = "192.168.154.112" # Замените на IP реального Antminer
PORT = 4028 # Стандартный порт API Antminer

def send_cgminer_cmd(ip, port, cmd):
    print(f"\n{'='*50}")
    print(f"Отправка команды: {cmd}")
    print(f"{'='*50}")
    
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        print("Подключение...")
        s.connect((ip, port))
        
        # Формируем команду для Antminer (cgminer API)
        payload = json.dumps({"command": cmd})
        print(f"Запрос отправлен: {payload}")
        s.sendall(payload.encode('utf-8'))
        
        # Читаем ответ
        full_response = b""
        while True:
            try:
                chunk = s.recv(4096)
                if not chunk:
                    break
                full_response += chunk
            except Exception as e:
                # Если сработал таймаут при чтении, но данные уже есть - прерываем цикл
                if full_response:
                    break
                else:
                    raise e
                    
        s.close()
        
        # Очищаем ответ от мусора (Antminer часто присылает нулевые байты \x00 в конце)
        clean_resp = full_response.decode('utf-8', errors='ignore').replace('\x00', '').strip()
        print(f"Фактически получено байт: {len(clean_resp)}")
        
        if not clean_resp:
            print("ОШИБКА: Пустой ответ.")
            return

        try:
            # Пробуем разобрать JSON
            parsed_json = json.loads(clean_resp)
            print("JSON успешно разобран. Форматированный вывод:")
            print(json.dumps(parsed_json, indent=2))
        except json.JSONDecodeError as e:
            print(f"ОШИБКА ПАРСИНГА JSON. Сырой ответ:")
            print(clean_resp)
            
    except socket.timeout:
        print("ОШИБКА: Таймаут (Асик не ответил за 5 секунд).")
    except ConnectionRefusedError:
        print("ОШИБКА: В соединении отказано (порт 4028 закрыт).")
    except Exception as e:
        print(f"СИСТЕМНАЯ ОШИБКА: {e}")

if __name__ == "__main__":
    print(f"Начинаем диагностику Antminer на IP: {IP}")
    
    # Список команд для проверки (ищем, где спрятан алгоритм)
    commands = [
        "summary",  # Базовая статистика (хешрейт, аптайм)
        "stats",    # Подробная статистика (кулеры, температуры, платы)
        "pools",    # Информация о пулах
        "config",   # Конфигурация устройства (тут может быть алгоритм!)
        "coin",     # Запрос монеты (работает на некоторых прошивках)
        "version"   # Версия прошивки и железа
    ]
    
    for cmd in commands:
        send_cgminer_cmd(IP, PORT, cmd)
        time.sleep(1) # Небольшая пауза, чтобы не заспамить асик