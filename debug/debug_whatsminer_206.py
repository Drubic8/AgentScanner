import json
import socket
import struct

# Укажите IP-адрес проблемного устройства
IP = "192.168.90.56"
PORT = 4433 # Стандартный порт Whatsminer API

def send_cmd(ip, port, cmd, param=None):
    print(f"\n{'='*50}")
    print(f"Отправка команды: {cmd} (param: {param})")
    print(f"{'='*50}")
    
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Ставим жесткий таймаут, чтобы зависший асик не повесил скрипт
        s.settimeout(5)
        print("Подключение...")
        s.connect((ip, port))
        print("Подключено! Формируем пакет...")
        
        payload = {"cmd": cmd}
        if param:
            payload["param"] = param
            
        json_str = json.dumps(payload)
        # Whatsminer API требует 4 байта длины пакета перед самим JSON
        pkg = struct.pack('<I', len(json_str)) + json_str.encode('utf-8')
        
        s.sendall(pkg)
        print("Запрос отправлен. Ждем ответ...")
        
        # Читаем длину ответа (первые 4 байта)
        len_data = s.recv(4)
        if not len_data or len(len_data) < 4:
            print("ОШИБКА: Асик оборвал соединение или вернул пустой ответ (нет длины пакета).")
            return
            
        pkg_len = struct.unpack('<I', len_data)[0]
        print(f"Асик сообщил, что отправит {pkg_len} байт данных.")
        
        if pkg_len > 1_000_000:
            print("ОШИБКА: Заявленный размер слишком велик (похоже на битые данные/мусор).")
            return

        # Читаем сами данные
        chunks = []
        bytes_recd = 0
        while bytes_recd < pkg_len:
            chunk = s.recv(min(pkg_len - bytes_recd, 4096))
            if not chunk:
                print("ВНИМАНИЕ: Соединение оборвалось до того, как мы получили все данные.")
                break
            chunks.append(chunk)
            bytes_recd += len(chunk)
            
        raw_resp = b''.join(chunks)
        print(f"Фактически получено байт: {len(raw_resp)}")
        
        try:
            # Пытаемся декодировать байты в текст, игнорируя битые символы
            decoded_resp = raw_resp.decode('utf-8', errors='ignore')
            print("\n--- СЫРОЙ ОТВЕТ (ТЕКСТ) ---")
            print(decoded_resp)
            print("---------------------------\n")
            
            # Пробуем разобрать JSON
            parsed_json = json.loads(decoded_resp)
            print("JSON успешно разобран. Форматированный вывод:")
            print(json.dumps(parsed_json, indent=2))
            
        except json.JSONDecodeError as e:
            print(f"ОШИБКА ПАРСИНГА JSON: Асик вернул невалидный JSON! Подробности: {e}")
            
    except socket.timeout:
        print("ОШИБКА ТАЙМАУТА: Асик не ответил за 5 секунд (завис).")
    except ConnectionRefusedError:
        print(f"ОШИБКА ПОДКЛЮЧЕНИЯ: Асик отверг запрос по порту {port}. Порт закрыт.")
    except Exception as e:
        print(f"СИСТЕМНАЯ ОШИБКА: {e}")
    finally:
        try:
            s.close()
        except:
            pass

if __name__ == "__main__":
    print(f"Начинаем диагностику Whatsminer на IP: {IP}")
    
    # 1. Запрос системной информации (здесь должны быть error-code)
    send_cmd(IP, PORT, "get.device.info")
    
    # 2. Запрос текущего статуса майнинга
    send_cmd(IP, PORT, "get.miner.status", "summary")
    
    # 3. Запрос пулов
    send_cmd(IP, PORT, "get.miner.status", "pools")