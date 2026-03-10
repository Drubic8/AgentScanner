import ipaddress
import re
from ..utils import get_uptime_str, normalize_hashrate

def parse_antminer_stock(ip, resp):
    r_sum = resp.get("summary", {})
    r_stats = resp.get("stats", {})
    r_ver = resp.get("get_version", {}) 
    
    # --- 1. СБОР СЫРЫХ ДАННЫХ ---
    stats_list = []
    if r_stats.get('STATS'):
        for item in r_stats['STATS']:
            if isinstance(item, dict): stats_list.append(item)
            
    summary_block = {}
    if r_sum.get('SUMMARY'):
        if isinstance(r_sum['SUMMARY'], list): summary_block = r_sum['SUMMARY'][0]
        elif isinstance(r_sum['SUMMARY'], dict): summary_block = r_sum['SUMMARY']

    # Плоский словарь
    flat_data = {}
    for item in stats_list: flat_data.update(item)
    flat_data.update(summary_block)

    # --- 2. ОПРЕДЕЛЕНИЕ МОДЕЛИ ---
    raw_type = flat_data.get('Type', flat_data.get('Miner', flat_data.get('Model', '')))
    if not raw_type or str(raw_type).strip() in ["", "Antminer"]:
        if r_ver.get('VERSION'):
            v_block = r_ver['VERSION'][0]
            raw_type = v_block.get('Type', v_block.get('Miner', raw_type))

    model_clean = str(raw_type).replace('Antminer', '').strip()
    if not model_clean: model_clean = "Unknown"
    model = f"Antminer {model_clean}"

    # --- 3. РЕЖИМ ПАРСИНГА ---
    has_chain = False
    for item in stats_list:
        if 'chain' in item or 'CHAIN' in item:
            has_chain = True
            break
            
    # --- 4. ТЕМПЕРАТУРЫ ---
    temps = []
    
    # === НОВАЯ АРХИТЕКТУРА (S19/L7/T21 с Chain) ===
    if has_chain:
        for item in stats_list:
            chains = item.get('chain', item.get('CHAIN', []))
            for chain in chains:
                chain_temps = []
                # Ищем PCB/Board
                for k, v in chain.items():
                    k_lower = k.lower()
                    if 'pcb' in k_lower or 'board' in k_lower:
                        if 'chip' not in k_lower:
                             if isinstance(v, list): chain_temps.extend(v)
                             else: chain_temps.append(v)

                # Если не нашли, ищем Temp
                if not chain_temps:
                    for k, v in chain.items():
                        k_lower = k.lower()
                        if 'temp' in k_lower and 'chip' not in k_lower:
                            if isinstance(v, list): chain_temps.extend(v)
                            else: chain_temps.append(v)

                for x in chain_temps:
                    try: 
                        tv = int(float(x))
                        if 15 < tv < 115: temps.append(tv)
                    except: pass

    # === СТАРАЯ АРХИТЕКТУРА (Z15) ===
    else:
        for i in range(1, 17):
            found_val = None
            keys_to_check = [f'temp_pcb{i}', f'pcb_temp{i}', f'temp{i}']
            for key in keys_to_check:
                val = flat_data.get(key)
                if val is not None:
                    try:
                        tv = int(float(val))
                        if 15 < tv < 115:
                            found_val = tv
                            break 
                    except: pass
            if found_val is not None:
                temps.append(found_val)

    temps_str = [str(t) for t in temps]

    # --- 5. ВЕНТИЛЯТОРЫ ---
    fans = []
    for i in range(1, 9):
        found_val = None
        for key in [f'fan{i}', f'fan{i}_rpm', f'fan{i}_speed']:
            if flat_data.get(key):
                try:
                    v = int(float(flat_data[key]))
                    if v > 0: 
                        found_val = v
                        break 
                except: pass
        if found_val:
            fans.append(str(found_val))

    if not fans:
        raw_fans = []
        for k, v in flat_data.items():
            if 'fan' in k.lower() and 'id' not in k.lower():
                try:
                    val = int(float(v))
                    if 500 < val < 15000: raw_fans.append(val)
                except: pass
        if raw_fans:
            fans = [str(f) for f in sorted(raw_fans)]

    # --- 6. ХЕШРЕЙТ И АЛГОРИТМ ---
    uptime = flat_data.get('Elapsed', flat_data.get('Uptime', 0))
    
    r_val = flat_data.get('GHS 5s', flat_data.get('MHS 5s', 0))
    a_val = flat_data.get('GHS av', flat_data.get('MHS av', 0))
    if r_val == 0:
        for item in stats_list:
            if item.get('GHS 5s'): r_val = item['GHS 5s']; break
            if item.get('MHS 5s'): r_val = item['MHS 5s']; break

    api_algo = flat_data.get('algo') or flat_data.get('algorithm') or flat_data.get('coin_type')
    final_algo = "Unknown"
    algo_unit = "SHA"
    m_upper = model.upper()

    if api_algo:
        s_algo = str(api_algo).upper().strip()
        if "BTC" in s_algo or "SHA" in s_algo: final_algo = "SHA-256"; algo_unit = "SHA"
        elif "LTC" in s_algo or "SCRYPT" in s_algo: final_algo = "Scrypt"; algo_unit = "SCRYPT"
        elif "KAS" in s_algo: final_algo = "kHeavyHash"
        elif "ETH" in s_algo: final_algo = "Etchash"; algo_unit = "ETCHASH"
        elif "EQUIHASH" in s_algo or "ZEC" in s_algo: final_algo = "Equihash"; algo_unit = "SOL"
        else: final_algo = str(api_algo)
    else:
        # === ИСПРАВЛЕНИЕ: ДОБАВЛЕН T21 и S19k ===
        if "Z15" in m_upper or "Z11" in m_upper or "Z9" in m_upper: final_algo = "Equihash"; algo_unit = "SOL"
        elif "L7" in m_upper or "L9" in m_upper: final_algo = "Scrypt"; algo_unit = "SCRYPT"
        elif "E9" in m_upper: final_algo = "Etchash"; algo_unit = "ETCHASH"
        elif "KS" in m_upper: final_algo = "kHeavyHash"
        elif "D9" in m_upper: final_algo = "X11"; algo_unit = "X11"
        elif any(x in m_upper for x in ["S19", "S21", "T19", "T21", "S9", "M30", "M50"]): 
            final_algo = "SHA-256"; algo_unit = "SHA"

    pool, worker = "", ""
    if resp.get("pools", {}).get('POOLS'):
        p = resp["pools"]['POOLS'][0]
        pool = p.get('URL',''); worker = p.get('User','')

    final_real, u_r = normalize_hashrate(r_val, algo_unit)
    final_avg, u_a = normalize_hashrate(a_val, algo_unit)
    
    # === ИСПРАВЛЕНИЕ: ЧИСТКА RawHash (ЗАПЯТАЯ -> ТОЧКА) ===
    try:
        raw_h = float(str(final_real).replace(',', '.').strip())
    except:
        raw_h = 0.0

    return {
        "IP": ip, "Make": "Bitmain", "Model": model, 
        "Uptime": get_uptime_str(uptime),
        "Real": f"{final_real} {u_r}", "Avg": f"{final_avg} {u_a}",
        "Fan": " ".join(fans)[:30],
        "Temp": " ".join(temps_str), 
        "Pool": pool.replace("stratum+tcp://", ""), "Worker": worker,
        "SortIP": int(ipaddress.IPv4Address(ip)),
        "Algo": final_algo,
        "RawHash": raw_h
    }