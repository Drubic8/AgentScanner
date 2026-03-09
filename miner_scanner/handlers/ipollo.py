import re
import ipaddress
from ..utils import get_uptime_str

MODEL_MAP = {
    "G220": "G1",       
    "G1": "G1 Mini",    
    "V1": "V1 Mini",
    "X1": "X1",
    "B1": "B1",
}

def parse_ipollo(ip, data):
    # 1. STATS
    stats_resp = data.get('stats', {})
    if not stats_resp or 'STATS' not in stats_resp:
        return None
    stats = stats_resp['STATS'][0]
    
    # 2. SUMMARY (Может отсутствовать)
    summary_resp = data.get('summary', {})
    summary = {}
    if summary_resp and 'SUMMARY' in summary_resp:
        summary = summary_resp['SUMMARY'][0]

    # --- МОДЕЛЬ ---
    raw_id = stats.get('ID', 'Unknown')
    model_name = MODEL_MAP.get(raw_id, raw_id)
    model = f"iPollo {model_name}"

    # --- АЛГОРИТМ ---
    raw_algo = stats.get('Algo', 'Unknown').lower()
    if raw_algo == 'mwc': 
        algo = "Cuckatoo31 (MWC)"
    elif raw_algo == 'grin': 
        algo = "Cuckatoo32 (GRIN)"
    elif raw_algo == 'ethash': 
        algo = "Etchash (ETC)"
    else:
        algo = raw_algo.upper()

    # --- ХЕШРЕЙТ ---
    # iPollo пишет Hashrate=37.61 и Unit=G/s
    unit = stats.get('Unit', 'M/s')
    try:
        real_val = float(stats.get('Hashrate', 0))
    except:
        real_val = 0.0
    
    # Пытаемся найти средний в SUMMARY
    avg_val = 0.0
    try:
        # iPollo часто пишет средний в MHS (даже если unit G/s)
        avg_raw = float(summary.get('MHS av', 0))
        if avg_raw == 0: avg_raw = float(summary.get('MHS2 av', 0))
        avg_val = avg_raw
    except:
        pass

    # [FIX] ЛОГИКА СРЕДНЕГО
    # Если SUMMARY нет или там пусто, берем текущий (real) как средний
    if avg_val == 0:
        avg_disp_val = real_val # Это уже в G/s (если unit=G/s)
    else:
        # Если avg_val пришел из SUMMARY, он скорее всего в MH/s (например 37000)
        # А real_val в G/s (37). Нужно привести к одному виду.
        if unit == 'G/s' and avg_val > real_val * 10: 
            # Если среднее в 1000 раз больше текущего, значит оно в MH/s, переводим в G/s
            avg_disp_val = avg_val / 1000.0
        else:
            avg_disp_val = avg_val

    # Формируем строки
    if unit == 'G/s':
        real_disp = f"{real_val:.2f} G/s"
        avg_disp = f"{avg_disp_val:.2f} G/s"
        # В базу пишем TH/s (для сортировки). 37 G/s = 0.037 TH/s
        raw_hash = real_val / 1000.0 
    else:
        real_disp = f"{real_val:.2f} MH/s"
        avg_disp = f"{avg_disp_val:.2f} MH/s"
        raw_hash = real_val / 1000000.0 

    # --- ТЕМПЕРАТУРА ---
    temp_str = stats.get('Temp', '')
    temps = re.findall(r"[\d\.]+", temp_str)
    temps = [str(int(float(t))) for t in temps]
    if len(temps) > 4: temps = [temps[0], temps[-1]] # Мин и Макс

    # --- ВЕНТИЛЯТОРЫ ---
    fan_str = stats.get('Fan', '')
    fans = re.findall(r"\d+", fan_str)

    # --- UPTIME ---
    uptime = int(stats.get('Elapsed', 0))

    # --- ПУЛ ---
    pools_resp = data.get('pools', {})
    pool = ""
    worker = ""
    if pools_resp and 'POOLS' in pools_resp:
        for p in pools_resp['POOLS']:
            pool = p.get('URL', '')
            worker = p.get('User', '')
            if pool: break
            
    pool = pool.replace("Stratum+tcp://", "").replace("stratum+tcp://", "")

    return {
        "IP": ip, 
        "Make": "iPollo", 
        "Model": model, 
        "Uptime": get_uptime_str(uptime),
        "Real": real_disp, 
        "Avg": avg_disp,
        "Fan": " ".join(fans), 
        "Temp": " ".join(temps), 
        "Pool": pool, 
        "Worker": worker,
        "SortIP": int(ipaddress.IPv4Address(ip)),
        "Algo": algo,
        "RawHash": raw_hash
    }