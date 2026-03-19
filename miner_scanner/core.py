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
from .handlers.antminer_stock import parse_antminer_stock, parse_antminer_web_fallback
from .handlers.antminer_vnish import parse_antminer_vnish
from .handlers.ipollo import parse_ipollo
from .handlers.elphapex import scan_elphapex
from .handlers.jasminer import parse_jasminer, fetch_jasminer_web  # ✅ ИМПОРТ ДОБАВЛЕН
from .handlers.cgminer_web import parse_cgminer_web

def send_avalon_cmd(ip, cmds):
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
                except: break
            s.close()
            text = full_response.decode('utf-8', errors='ignore').strip('\x00')
            if text:
                data[cmd_key['command']] = [text]
        except:
            pass
    return data

def process_ip(ip, target_makes=None):
    t0 = time.perf_counter() # Запускаем микро-таймер
    res = _process_ip_internal(ip, target_makes)
    
    if res:
        res['ScanTime'] = round(time.perf_counter() - t0, 3) 
    return res

def _process_ip_internal(ip, target_makes=None):
    if target_makes is None:
        target_makes = ["Bitmain", "MicroBT", "Elphapex", "Canaan", "iPollo", "Jasminer"]

    # =========================================================
    # 1. ЖЕСТКАЯ ФИЛЬТРАЦИЯ (Уникальные порты)
    # =========================================================
    if check_port(ip, 4433):
        if "MicroBT" in target_makes:
            res = parse_whatsminer_v3(ip)
            if res: return res
        return None 

    if check_port(ip, 9588):
        if "Elphapex" in target_makes:
            res = scan_elphapex(ip, port_9588_open=True)
            if res: return res
        return None

    # =========================================================
    # 🚀 2. ЭКСПРЕСС-ПРОВЕРКА JASMINER (Порт 80)
    # =========================================================
    # Бьем сразу в эндпоинт из Wireshark. Если это Antminer на 80 порту, 
    # он просто отдаст 404 за доли секунды, вернет None и пойдет на проверку ниже.
    if "Jasminer" in target_makes and check_port(ip, 80):
        j_data = fetch_jasminer_web(ip)
        if j_data:
            return parse_jasminer(ip, j_data)

    # =========================================================
    # 3. СТАНДАРТНЫЕ ASIC'И (Порт 4028)
    # =========================================================
    needs_4028 = any(m in target_makes for m in ["Bitmain", "Canaan", "iPollo", "Jasminer"])
    if needs_4028 and check_port(ip, 4028):
        
        sock_data = get_socket_data(ip)
        resp = {}
        full_dump = ""
        
        if isinstance(sock_data, tuple) and len(sock_data) == 2:
            resp, full_dump = sock_data
        elif sock_data:
            resp = sock_data
            full_dump = json.dumps(resp).lower() 

        if resp:
            if "Canaan" in target_makes and "canaan" in full_dump:
                return parse_avalon(ip, resp)

            if "iPollo" in target_makes:
                stats_section = resp.get('stats', {}).get('STATS', [])
                if stats_section:
                    first_stat = stats_section[0]
                    if 'ID' in first_stat and str(first_stat['ID']).startswith('G'):
                        return parse_ipollo(ip, resp)
                    if 'G-Model' in first_stat:
                        return parse_ipollo(ip, resp)

            if "Jasminer" in target_makes and "jasminer" in full_dump:
                return parse_jasminer(ip, resp)
            
            if "Bitmain" in target_makes and "vnish" in full_dump:
                return parse_antminer_vnish(ip, resp)

            if "Bitmain" in target_makes:
                # 🛡️ ПРАВИЛЬНАЯ ЗАЩИТА НА ПОРТУ 4028
                if not any(x in full_dump for x in ["canaan", "jasminer", "ipollo", "g-model", "hammer", "bluestar"]):
                    if "SUMMARY" in resp.get("summary", {}) or "STATS" in resp.get("stats", {}):
                        return parse_antminer_stock(ip, resp)

    # =========================================================
    # 4. ФОЛЛБЭК ДЛЯ СПЯЩИХ И ЗАВИСШИХ (Порт 80)
    # =========================================================
    needs_80 = any(m in target_makes for m in ["Bitmain", "Canaan", "Jasminer", "iPollo", "Elphapex"])
    if needs_80 and check_port(ip, 80):
        
        if "Elphapex" in target_makes:
            res = scan_elphapex(ip, port_9588_open=False)
            if res: return res

        # Jasminer исключен отсюда, так как уже прошел экспресс-проверку в шаге 2
        if "Canaan" in target_makes or "iPollo" in target_makes:
            cg_res = parse_cgminer_web(ip)
            if cg_res: return cg_res

        if "Bitmain" in target_makes:
            from .handlers.antminer_stock import parse_antminer_web_fallback
            ant_web_res = parse_antminer_web_fallback(ip)
            if ant_web_res: return ant_web_res

    return None

def scan_network_range(ip_range_str, target_makes=None):
    ip_list = parse_ip_range(ip_range_str)
    results = []
    
    with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        futures = {executor.submit(process_ip, ip, target_makes): ip for ip in ip_list}
        
        for future in concurrent.futures.as_completed(futures):
            try:
                res = future.result()
                if res:
                    results.append(res)
            except Exception as e:
                pass
                
    return results