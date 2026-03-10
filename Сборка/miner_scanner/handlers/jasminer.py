import ipaddress
from ..utils import get_uptime_str, normalize_hashrate

def parse_jasminer(ip, resp):
    # Теперь у нас есть 3 источника данных:
    # 1. summary (хешрейт)
    # 2. pools (воркер)
    # 3. boards (кулеры, температуры)
    
    # --- 1. ХЕШРЕЙТ и МОДЕЛЬ (из summary) ---
    sum_data = resp.get('summary', {})
    if isinstance(sum_data, list): sum_data = sum_data[0]

    model = sum_data.get("miner", "JasMiner Unknown")
    uptime = sum_data.get("uptime", 0)
    
    def parse_h(val):
        if not val: return 0.0
        try: return float(str(val).split()[0])
        except: return 0.0

    r_val = parse_h(sum_data.get("rt"))
    a_val = parse_h(sum_data.get("avg"))
    
    # --- 2. ВЕНТИЛЯТОРЫ и ТЕМПЕРАТУРЫ (из boards) ---
    boards_root = resp.get('boards', {})
    # Иногда это список, иногда словарь
    if isinstance(boards_root, list): boards_root = boards_root[0]
    
    fans = []
    temps = []
    
    # A. Вентиляторы (лежат в корне boards: fan1, fan2...)
    # В логе было видно: fan1: 1080, fan4: 0
    for i in range(1, 9):
        key = f"fan{i}"
        if key in boards_root:
            try:
                v = int(float(str(boards_root[key])))
                if v > 0: fans.append(str(v))
            except: pass
            
    # B. Температуры (лежат внутри списка 'board')
    board_list = boards_root.get('board', [])
    if isinstance(board_list, list):
        for b in board_list:
            # Собираем температуры чипов (asic0_temp...)
            for k, v in b.items():
                if '_temp' in k and 'asic' in k: # asic0_temp, asic1_temp
                    try: temps.append(int(float(v)))
                    except: pass
    
    # Если в boards температур не нашли, берем из summary (temp_min/max)
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
        # Ищем активный пул (In use или Alive)
        active_pool = None
        for p in pools_list:
            status = str(p.get('status', '')).lower()
            if status == 'in use' or status == 'alive':
                active_pool = p
                break
        
        # Если активного нет, берем первый (pool: 0)
        if not active_pool and len(pools_list) > 0:
            active_pool = pools_list[0]
            
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