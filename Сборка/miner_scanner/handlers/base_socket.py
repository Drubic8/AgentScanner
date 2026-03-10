import socket
import json
import time
import re
from ..config import SOCKET_PORT, TIMEOUT, RETRY_COUNT, BUFFER_SIZE

def repair_json(broken_json):
    """Починка битого JSON (как у Z15)"""
    s = broken_json
    s = re.sub(r'(\d+)\s+"', r'\1, "', s)
    s = re.sub(r'"\s+"', r'", "', s)
    s = re.sub(r'(}|])\s+"', r'\1, "', s)
    s = re.sub(r',\s*}', '}', s)
    s = re.sub(r',\s*]', ']', s)
    return s

def send_socket_cmd(ip, cmd, raw_mode=False):
    """
    Версия с экспоненциальной паузой. 
    Если асик тупит, следующая попытка будет через более длинную паузу.
    """
    for attempt in range(RETRY_COUNT):
        sock = None
        try:
            # Если это не первая попытка, даем асику время отдохнуть
            if attempt > 0:
                # Попытка 1: пауза 1 сек, Попытка 2: пауза 2 сек
                time.sleep(1.0 * attempt) 

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(TIMEOUT) 
            sock.connect((ip, SOCKET_PORT))
            
            if raw_mode:
                payload = cmd.encode('utf-8')
            else:
                payload = json.dumps({"command": cmd, "parameter": ""}).encode('utf-8')
            
            sock.sendall(payload)
            
            data = b""
            while True:
                chunk = sock.recv(BUFFER_SIZE)
                if not chunk: break
                data += chunk
                if b'\x00' in chunk: break
                # Защита: если получили закрывающую скобку и данных достаточно
                if data.strip().endswith(b'}') and len(data) > 10: break
            
            sock.close()
            dec = data.decode('utf-8', errors='ignore').replace('\x00', '').strip()
            
            # Если ответ пустой — это повод для повторной попытки
            if not dec or len(dec) < 5:
                continue 

            if "}{" in dec: dec = dec.replace("}{", ", ")
            
            try:
                # Проверяем, что это валидный JSON
                parsed = json.loads(dec)
                if not parsed: continue # Если пустой объект, пробуем еще раз
                return parsed
            except json.JSONDecodeError:
                try:
                    fixed_dec = repair_json(dec)
                    return json.loads(fixed_dec)
                except: 
                    continue # Ошибка в JSON — пробуем перечитать

        except (socket.timeout, ConnectionRefusedError, OSError):
            continue # Пауза теперь в начале следующей итерации
        except Exception:
            continue
        finally:
            if sock: 
                try: sock.close() 
                except: pass
    return None

def get_socket_data(ip):
    # 1. Стандартный опрос (Antminer / Whatsminer)
    commands = ["get_miner_info", "get_version", "devdetails", "summary", "stats", "devs", "edevs", "pools"]
    resp = {}
    
    for cmd in commands:
        res = send_socket_cmd(ip, cmd)
        if res: 
            resp[cmd] = res

    # 2. === JASMINER FALLBACK (TEXT MODE) ===
    # Если JSON-запросы не вернули stats/summary, включаем режим JasMiner
    if not resp.get('summary') and not resp.get('stats'):
        # ДОБАВИЛИ "boards" В СПИСОК КОМАНД!
        text_cmds = ["summary", "pools", "boards"]
        
        for t_cmd in text_cmds:
            jas_res = send_socket_cmd(ip, t_cmd, raw_mode=True)
            if jas_res:
                resp[t_cmd] = jas_res
    
    return resp