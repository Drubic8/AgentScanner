import ipaddress
import requests
from ..utils import get_uptime_str, normalize_hashrate

def get_vnish_web_api(ip):
    """Легкий опрос веб-API только для алгоритма, статуса и ошибок"""
    api_data = {"summary": {}, "info": {}}
    try:
        r_sum = requests.get(f"http://{ip}/api/v1/summary", timeout=2)
        if r_sum.status_code == 200:
            api_data["summary"] = r_sum.json()
            
        r_info = requests.get(f"http://{ip}/api/v1/info", timeout=2)
        if r_info.status_code == 200:
            api_data["info"] = r_info.json()
    except:
        pass
    return api_data

def parse_antminer_vnish(ip, resp):
    # ==========================================
    # 1. БАЗА ИЗ ПОРТА 4028 (Как было раньше)
    # ==========================================
    r_sum = resp.get("summary", {})
    r_stats = resp.get("stats", {})
    
    sum_block = {}
    if r_sum.get('SUMMARY'): sum_block = r_sum['SUMMARY'][0]
    
    stats_block = {}
    if r_stats.get('STATS'):
        for item in r_stats['STATS']:
            if isinstance(item, dict): stats_block.update(item)

    # --- Модель ---
    raw_type = stats_block.get('Type', sum_block.get('Model', 'Unknown'))
    raw_type = str(raw_type).replace("Antminer", "").strip()
    model = f"Antminer {raw_type}".strip()
    if "Vnish" not in model: model += " (Vnish)"
    
    # --- Аптайм (секунды -> Xd Xh Xm) ---
    uptime_sec = int(sum_block.get('Elapsed', stats_block.get('Elapsed', 0)))

    # --- Хешрейт (сырой) ---
    r_val = sum_block.get('GHS 5s', sum_block.get('MHS 5s', 0))
    if r_val == 0: r_val = stats_block.get('GHS 5s', stats_block.get('MHS 5s', 0))
    
    a_val = sum_block.get('GHS av', sum_block.get('MHS av', 0))
    if a_val == 0: a_val = stats_block.get('GHS av', stats_block.get('MHS av', 0))

    # --- Кулеры ---
    fans = []
    for i in range(1, 9):
        f = stats_block.get(f'fan{i}')
        if f and str(f).isdigit() and int(f) > 0:
            fans.append(str(f))

    # --- Температуры ---
    temps = []
    for i in range(1, 9):
        t = stats_block.get(f'temp2_{i}') or stats_block.get(f'temp_chip{i}') or stats_block.get(f'temp_{i}')
        if t: 
            try: temps.append(str(int(t)))
            except: pass

    # --- Пулы и Воркер ---
    pool, worker = "", ""
    if resp.get("pools", {}).get('POOLS'):
        for p in resp["pools"]['POOLS']:
            # Пропускаем отключенные и пул разработчика DevFee
            if p.get("Status", "Alive") == "Alive" and "devfee" not in p.get("URL", "").lower():
                pool = p.get('URL', '')
                worker = p.get('User', '')
                break

    # --- Аппаратные ошибки (крестики 'x' из 4028) ---
    has_hw_error = False
    failed_boards = [] 
    for i in range(1, 9):
        chain_key = f"chain_acs{i}"
        if chain_key in stats_block:
            if 'x' in str(stats_block[chain_key]).lower():
                has_hw_error = True
                failed_boards.append(str(i))
                
    error_str = f"HW ERR (B{','.join(failed_boards)})" if has_hw_error else ""
    error_details = f"Отвал чипов ('x') на плате {','.join(failed_boards)}" if has_hw_error else ""

    # ==========================================
    # 2. НАДСТРОЙКА ИЗ ПОРТА 80 (API VNISH)
    # ==========================================
    api_data = get_vnish_web_api(ip)
    miner_info = api_data.get("info", {})
    miner_sum = api_data.get("summary", {}).get("miner", {})
    
    # --- Алгоритм ---
    api_algo = miner_info.get("algorithm", stats_block.get("algo", ""))
    
    # Задаем дефолтные значения
    final_algo, algo_unit = "SHA-256", "SHA"
    
    if api_algo:
        s_algo = str(api_algo).lower()
        if "sha" in s_algo:       final_algo, algo_unit = "SHA-256", "SHA"
        elif "scrypt" in s_algo:  final_algo, algo_unit = "Scrypt", "SCRYPT"
        elif "kheavy" in s_algo:  final_algo, algo_unit = "kHeavyHash", "HEAVY" # Под будущие кастомы на KASPA
        else:                     final_algo = s_algo.upper()
    else:
        # Фоллбэк: если API недоступно, определяем по названию модели
        m_upper = model.upper()
        if any(x in m_upper for x in ["L3", "L7", "L9"]): 
            final_algo, algo_unit = "Scrypt", "SCRYPT"
        elif any(x in m_upper for x in ["S19", "S21", "T21", "T19", "S9"]): 
            final_algo, algo_unit = "SHA-256", "SHA"

    # --- Нормализация Хешрейта (на основе данных 4028) ---
    final_real, u_r = normalize_hashrate(r_val, algo_unit)
    final_avg, u_a = normalize_hashrate(a_val, algo_unit)

    # --- Статусы и Ошибки ---
    status = "Starting"

    if miner_sum:
        status_block = miner_sum.get("miner_status", {})
        m_state = str(status_block.get("miner_state", "")).lower()
        m_desc = str(status_block.get("description", "")).strip()
        m_code = str(status_block.get("failure_code", ""))

       # Маппинг статусов (с учетом особенностей VNish)
        if m_state == "mining": status = "Running"
        elif m_state in ["sleep", "stopped", "paused", "standby"]: status = "Sleep"
        elif m_state == "tuning": status = "WaitWork"
        elif m_state == "failure": status = "Error"
        else: status = "Starting"

        # Глобальная ошибка (failure_code)
        if m_state == "failure" and m_desc:
            err_code = f"ERR [{m_code}]" if m_code else "ERR"
            error_str = f"{error_str} + {err_code}" if error_str else err_code
            error_details = f"{error_details}\n{err_code}: {m_desc}".strip()

        # Детальные ошибки по платам
        chains = miner_sum.get("chains", [])
        for i, chain in enumerate(chains):
            c_stat = chain.get("status", {})
            if str(c_stat.get("state", "")).lower() == "failure":
                c_desc = c_stat.get("description", "Unknown error")
                board_err = f"ERR (B{i})"
                error_str = f"{error_str} + {board_err}" if error_str else board_err
                error_details = f"{error_details}\nПлата {i} = \"{c_desc}\"".strip()
                # На всякий случай переводим статус в Error, если плата упала
                status = "Error" 
    else:
        # Если порт 80 не ответил, применяем стандартную логику
        if float(r_val) > 0: status = "Running"
        else:
            status = "Error"
            if not error_str:
                error_str = "NO HASH"
                error_details = "Хешрейт 0, Web API недоступно"

    try: raw_h = float(str(final_real).replace(',', '.').strip())
    except: raw_h = 0.0

    return {
        "IP": ip, 
        "Make": "Bitmain", 
        "Model": model, 
        "Uptime": get_uptime_str(uptime_sec),
        "Real": f"{raw_h} {u_r}", 
        "Avg": f"{final_avg} {u_a}",
        "Fan": " ".join(fans), 
        "Temp": " ".join(temps), 
        "Pool": pool.replace("stratum+tcp://", ""), 
        "Worker": worker,
        "SortIP": int(ipaddress.IPv4Address(ip)),
        "Algo": final_algo,
        "Status": status,
        "Error": error_str.strip(' +'),
        "ErrorDetails": error_details.strip(),
        "RawHash": raw_h
    }