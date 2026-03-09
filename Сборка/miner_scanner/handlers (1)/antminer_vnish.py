import ipaddress
from ..utils import get_uptime_str, normalize_hashrate

def parse_antminer_vnish(ip, resp):
    r_sum = resp.get("summary", {})
    r_stats = resp.get("stats", {})
    
    sum_block = {}
    if r_sum.get('SUMMARY'): sum_block = r_sum['SUMMARY'][0]
    elif r_sum.get('Msg') and isinstance(r_sum['Msg'], dict): sum_block = r_sum['Msg']
    
    stats_block = {}
    if r_stats.get('STATS'):
        for item in r_stats['STATS']:
            if isinstance(item, dict): stats_block.update(item)

    raw_type = stats_block.get('Type', stats_block.get('Miner', sum_block.get('Model', '')))
    raw_type = str(raw_type).replace("Antminer", "").strip()
    model = f"Antminer {raw_type}".strip()
    if "Vnish" not in model: model += " (Vnish)"
    
    uptime_sec = sum_block.get('Elapsed', 0)
    if uptime_sec == 0: uptime_sec = stats_block.get('Elapsed', 0)

    r_val = sum_block.get('GHS 5s', sum_block.get('MHS 5s', 0))
    a_val = sum_block.get('GHS av', sum_block.get('MHS av', 0))
    if r_val == 0: r_val = stats_block.get('GHS 5s', stats_block.get('MHS 5s', 0))
    if a_val == 0: a_val = stats_block.get('GHS av', stats_block.get('MHS av', 0))
    
    val_real = r_val
    val_avg = a_val
    
    # --- ОПРЕДЕЛЕНИЕ АЛГОРИТМА ---
    api_algo = stats_block.get('algo') or sum_block.get('algo') or \
               stats_block.get('algorithm') or stats_block.get('coin_type')
    
    final_algo = "Unknown"
    algo_unit = "SHA"

    if api_algo:
        s_algo = str(api_algo).upper().strip()
        if "SHA" in s_algo or "BTC" in s_algo: final_algo = "SHA-256"; algo_unit = "SHA"
        elif "SCRYPT" in s_algo or "LTC" in s_algo: final_algo = "Scrypt"; algo_unit = "SCRYPT"
        elif "X11" in s_algo: final_algo = "X11"; algo_unit = "X11"
        elif "KAS" in s_algo or "HEAVY" in s_algo: final_algo = "kHeavyHash"
        elif "ETH" in s_algo: final_algo = "Etchash"; algo_unit = "M"
        elif "EQUIHASH" in s_algo or "ZEC" in s_algo: final_algo = "Equihash"; algo_unit = "SOL"
        else: final_algo = str(api_algo)
    else:
        # Fallback по модели (ИСПРАВЛЕНО: Добавлен T21)
        m_upper = model.upper()
        if "L7" in m_upper or "L9" in m_upper: final_algo = "Scrypt"; algo_unit = "SCRYPT"
        elif "D9" in m_upper: final_algo = "X11"; algo_unit = "X11"
        elif "E9" in m_upper: final_algo = "Etchash"; algo_unit = "M"
        elif "KS" in m_upper: final_algo = "kHeavyHash"
        # Добавил T21, T19, S9
        elif any(x in m_upper for x in ["S19", "S21", "T21", "T19", "S9"]): 
            final_algo = "SHA-256"; algo_unit = "SHA"

    # Fans
    fans = []
    for i in range(1, 9):
        for key in [f'fan{i}', f'fan{i}_rpm', f'fan{i}_speed']:
            if stats_block.get(key):
                try:
                    v = int(float(stats_block[key]))
                    if v > 0: fans.append(str(v)); break
                except: pass
    if not fans and 'Fan Speed In' in sum_block:
        fans.append(str(sum_block['Fan Speed In']))

    # Temps
    temps = []
    found_temps = False
    if 'chain' in stats_block:
        for c in stats_block['chain']:
            t = c.get('temp_pcb', c.get('temp_board'))
            if t:
                if isinstance(t, list): temps.extend([int(x) for x in t])
                elif isinstance(t, (int, float)): temps.append(int(t))
                elif isinstance(t, str): 
                    try: temps.extend([int(x) for x in t.replace('-',' ').split()])
                    except: pass
                found_temps = True
    if not found_temps:
        for i in range(1, 9):
            t = stats_block.get(f'temp2_{i}')
            if not t: t = stats_block.get(f'temp_pcb_{i}')
            if t: 
                try: temps.append(int(t))
                except: pass

    pool, worker = "", ""
    if resp.get("pools", {}).get('POOLS'):
        p = resp["pools"]['POOLS'][0]
        pool = p.get('URL',''); worker = p.get('User','')
    elif r_sum.get('POOLS'):
        p = r_sum['POOLS'][0]
        pool = p.get('URL',''); worker = p.get('User','')

    final_real, u_r = normalize_hashrate(val_real, algo_unit)
    final_avg, u_a = normalize_hashrate(val_avg, algo_unit)

    # === ИСПРАВЛЕНИЕ ЗАПЯТОЙ ===
    try:
        raw_h = float(str(final_real).replace(',', '.').strip())
    except:
        raw_h = 0.0

    return {
        "IP": ip, "Make": "Bitmain", "Model": model, 
        "Uptime": get_uptime_str(uptime_sec),
        "Real": f"{final_real} {u_r}", "Avg": f"{final_avg} {u_a}",
        "Fan": " ".join(fans), "Temp": " ".join(str(t) for t in temps), 
        "Pool": pool.replace("stratum+tcp://", ""), "Worker": worker,
        "SortIP": int(ipaddress.IPv4Address(ip)),
        "Algo": final_algo,
        "RawHash": raw_h
    }