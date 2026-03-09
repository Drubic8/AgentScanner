import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
import json
import socket
import time

from .config import MAX_THREADS
from .utils import check_port, parse_ip_range
from .handlers.base_socket import get_socket_data

# === ИМПОРТЫ ХЕНДЛЕРОВ ===
from .detect import get_miner_make
from .handlers.whatsminer_v3 import parse_whatsminer_v3
from .handlers.avalon import parse_avalon
from .handlers.antminer_stock import parse_antminer_stock
from .handlers.antminer_vnish import parse_antminer_vnish
from .handlers.ipollo import parse_ipollo
from .handlers.elphapex import scan_elphapex
from .handlers.jasminer import parse_jasminer
from .handlers.cgminer_web import parse_cgminer_web

def send_avalon_cmd(ip, cmds):
    # (Функция без изменений, оставляем как есть)
    data = {}
    for cmd_key in cmds:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2) 
            s.connect((ip, 4028))
            s.sendall(json.dumps(cmd_key).encode('utf-8'))
            full_response = b""
            while True:
                try:
                    chunk = s.recv(4096)
                    if not chunk: break
                    full_response += chunk
                    if full_response.strip().endswith(b'}') and full_response.count(b'{') == full_response.count(b'}'): break
                except: break
            s.close()
            decoded = json.loads(full_response.decode('utf-8', errors='ignore').replace('\x00', '').strip())
            key_name = list(cmd_key.values())[0]
            data[key_name] = decoded
        except: pass
    return data

def process_ip(ip):
    # 1. WHATSMINER V3 (Специфические порты)
    if check_port(ip, 4433):
        res = parse_whatsminer_v3(ip, port=4433)
        if res: return res
        
    # 2. STANDARD MINERS (Port 4028)
    if check_port(ip, 4028):
        # --- ФИКС ДЛЯ ANTMINER: DOUBLE CHECK ---
        resp = get_socket_data(ip)
        
        if not resp:
            # Если с первого раза не ответил, ждем и пробуем второй раз
            time.sleep(1.5)
            resp = get_socket_data(ip)
        
        # Если после переопроса всё еще пусто
        if not resp:
            # Проверяем, может это Avalon (у него своя логика команд, если стандартные не прошли)
            maker = get_miner_make(ip)
            if maker == "Canaan":
                payloads = [{"command": "stats"}, {"command": "version"}, {"command": "pools"}]
                data = send_avalon_cmd(ip, payloads)
                return parse_avalon(ip, data)
            
            # Если порт 4028 открыт, но это не Авалон и он не отвечает на JSON,
            # значит это "тупящий" Antminer. Выходим здесь, чтобы не уйти на порт 80.
            return None
        # ---------------------------------------

        full_dump = json.dumps(resp).lower()
        
        # === [NEW] AVALON DETECT В ОСНОВНОМ ПОТОКЕ ===
        # Если асик ответил на стандартные команды (stats), но это Avalon
        if "avalon" in full_dump or "canaan" in full_dump or "mm id" in full_dump:
             return parse_avalon(ip, resp)
        # =============================================

        # --- IPOLLO DETECT ---
        stats_section = resp.get('stats', {}).get('STATS', [])
        if stats_section:
            first_stat = stats_section[0]
            if 'ID' in first_stat and str(first_stat['ID']).startswith('G'):
                return parse_ipollo(ip, resp)
            if 'G-Model' in first_stat:
                return parse_ipollo(ip, resp)

        # --- КЛАССИФИКАЦИЯ SOCKET-МАЙНЕРОВ ---
        if "jasminer" in full_dump: return parse_jasminer(ip, resp)
        if "vnish" in full_dump: return parse_antminer_vnish(ip, resp)
        
        # По умолчанию считаем Antminer Stock
        return parse_antminer_stock(ip, resp)

   # 3. WEB MINERS (Elphapex & CGminer Web: Hammer/Bluestar)
    if check_port(ip, 80):
        # Сначала пробуем Elphapex
        res = scan_elphapex(ip)
        if res: return res
        
        # Если это не Elphapex, пробуем универсальный парсер CGminer Web
        cg_res = parse_cgminer_web(ip)
        if cg_res: 
            return cg_res

    return None

def scan_network_range(ip_range_str):
    ip_list = parse_ip_range(ip_range_str)
    results = []
    with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        futures = {executor.submit(process_ip, ip): ip for ip in ip_list}
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res: results.append(res)
    if results: results.sort(key=lambda x: x['SortIP'])
    return results