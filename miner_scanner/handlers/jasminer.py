import ipaddress
import requests
from requests.auth import HTTPDigestAuth
from ..utils import get_uptime_str, normalize_hashrate

def fetch_jasminer_web(ip):
    """
    Молниеносный веб-запрос напрямую к API Jasminer.
    Используем сессию для пропуска двойного TCP-рукопожатия.
    """
    # Используем сессию для мгновенной Digest-авторизации
    session = requests.Session()
    session.auth = HTTPDigestAuth("root", "root")
    
    # Используем эндпоинт из Wireshark дампа и метод POST
    url = f"http://{ip}/cgi-bin/minerStatus.cgi"
    try:
        # Таймаут 2 сек. POST отрабатывает за доли секунды.
        resp = session.post(url, timeout=2.0)
        if resp.status_code == 200:
            return resp.json()
    except:
        pass
    finally:
        session.close() # Обязательно закрываем сессию
        
    return None

def parse_jasminer(ip, resp):
    """Парсинг JSON данных, полученных от Jasminer"""
    if not resp: return None
    
    # --- 1. ХЕШРЕЙТ и МОДЕЛЬ (из summary) ---
    sum_data = resp.get('summary', {})
    if isinstance(sum_data, list): sum_data = sum_data[0]

    model = sum_data.get("miner", "JasMiner Unknown")
    uptime = sum_data.get("uptime", 0)
    
    def parse_h(val):
        if not val: return 0.0
        try: return float(str(val).split()[0])
        except: return 0.0

    # Берем Real-time (rt) и Average (avg) хешрейт
    r_val = parse_h(sum_data.get("rt"))
    a_val = parse_h(sum_data.get("avg"))
    
    # --- 2. ВЕНТИЛЯТОРЫ и ТЕМПЕРАТУРЫ (из boards) ---
    boards_root = resp.get('boards', {})
    if isinstance(boards_root, list): boards_root = boards_root[0]
    
    fans = []
    temps = []
    
    # Собираем обороты вентиляторов
    for i in range(1, 9):
        key = f"fan{i}"
        if key in boards_root:
            try:
                v = int(float(str(boards_root[key])))
                if v > 0: fans.append(str(v))
            except: pass
            
    # Собираем температуры чипов
    board_list = boards_root.get('board', [])
    if isinstance(board_list, list):
        for b in board_list:
            for k, v in b.items():
                if '_temp' in k and 'asic' in k:
                    try: temps.append(int(float(v)))
                    except: pass
    
    if not temps:
        if sum_data.get("temp_min"): temps.append(int(sum_data["temp_min"]))
        if sum_data.get("temp_max"): temps.append(int(sum_data["temp_max"]))
        
    temps.sort()

    # --- 3. ПУЛ и ВОРКЕР (из pools) ---
    pool_url = ""
    worker = ""
    
    pools_root = resp.get('pools', {})
    pools_list = pools_root.get('pool') if isinstance(pools_root, dict) else pools_root
    
    if isinstance(pools_list, list) and pools_list:
        active_pool = next((p for p in pools_list if str(p.get('status', '')).lower() in ['in use', 'alive']), pools_list[0])
        if active_pool:
            pool_url = active_pool.get('url', '')
            worker = active_pool.get('user', '')

    pool_url = pool_url.replace("stratum+tcp://", "").replace("stratum+ssl://", "")

    # --- ФИНАЛИЗАЦИЯ ---
    final_real, u_r = normalize_hashrate(r_val, "ETCHASH")
    final_avg, u_a = normalize_hashrate(a_val, "ETCHASH")

    return {
        "IP": ip, "Make": "JasMiner", "Model": model, 
        "Uptime": get_uptime_str(uptime),
        "Real": f"{final_real} {u_r}", "Avg": f"{final_avg} {u_a}",
        "Fan": " ".join(fans), 
        "Temp": " ".join(str(t) for t in temps), 
        "Pool": pool_url, "Worker": worker,
        "SortIP": int(ipaddress.IPv4Address(ip)),
        "Algo": "Etchash",
        "RawHash": float(str(final_real).replace(',',''))
    }
