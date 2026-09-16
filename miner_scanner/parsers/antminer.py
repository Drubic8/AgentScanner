import ipaddress
import re
from ..utils import get_uptime_str, normalize_hashrate

def parse_antminer_stock(ip, resp, diagnostics=("", ""), work_mode=None):
    summary_block = {}
    if resp.get("summary", {}).get('SUMMARY'):
        summary_block = resp["summary"]['SUMMARY'][0]

    flat_data = {}
    if resp.get("stats", {}).get('STATS'):
        for item in resp["stats"]['STATS']:
            if isinstance(item, dict):
                flat_data.update(item)

    # === БАЗОВЫЕ ДАННЫЕ ===
    r_val = float(summary_block.get('GHS 5s', summary_block.get('MHS 5s', 0)))
    a_val = float(summary_block.get('GHS av', summary_block.get('MHS av', 0)))
    uptime_sec = int(summary_block.get('Elapsed', flat_data.get('Elapsed', 0)))

    # === АНАЛИЗ ПУЛОВ ===
    pool, worker = "", ""
    pools_disabled = False 
    
    if resp.get("pools", {}).get('POOLS'):
        pools_list = resp["pools"]['POOLS']
        if pools_list:
            p = pools_list[0]
            pool = p.get('URL', '')
            worker = p.get('User', '')
            
            if all(str(px.get('Status', '')).lower() in ["disabled", "deed", "dead"] for px in pools_list):
                pools_disabled = True

    is_sleeping = str(work_mode) == "1"

    # === 1. ОПРЕДЕЛЯЕМ МОДЕЛЬ ===
    raw_type = flat_data.get('Type', summary_block.get('Type', ''))
    model = str(raw_type).replace("Antminer", "").strip()
    final_model = f"Antminer {model}" if model else "Antminer Unknown"

    # === 2. ЛОГИКА АЛГОРИТМОВ ===
    api_algo = flat_data.get('algo', summary_block.get('algo', ''))
    final_algo = "SHA-256"

    if api_algo:
        s_algo = str(api_algo).upper().strip()
        if "SHA" in s_algo or "BTC" in s_algo: final_algo = "SHA-256"
        elif "SCRYPT" in s_algo or "LTC" in s_algo: final_algo = "Scrypt"
        elif "X11" in s_algo: final_algo = "X11"
        elif "KAS" in s_algo or "HEAVY" in s_algo: final_algo = "kHeavyHash"
        elif "ETH" in s_algo: final_algo = "Etchash"
        elif "EQUIHASH" in s_algo or "ZEC" in s_algo: final_algo = "Equihash"
        else: final_algo = str(api_algo)
    else:
        m_upper = final_model.upper()
        if any(x in m_upper for x in ["L3", "L7", "L9"]): final_algo = "Scrypt"
        elif "D9" in m_upper or "D7" in m_upper: final_algo = "X11"
        elif "E9" in m_upper: final_algo = "Etchash"
        elif "KS" in m_upper: final_algo = "kHeavyHash"
        elif "K7" in m_upper: final_algo = "Eaglesong"
        elif any(x in m_upper for x in ["Z15", "Z11", "Z9"]): final_algo = "Equihash"
        elif any(x in m_upper for x in ["S19", "S21", "T21", "T19", "S9"]): final_algo = "SHA-256"

    # === 3. ТОЧНЫЙ ФОРМАТТЕР ХЕШРЕЙТА ===
    def format_hr(val, algo, current_model):
        if algo == "SHA-256": return f"{val/1000:.2f}", "TH/s"
        elif algo == "Scrypt": 
            if val < 500: return f"{val:.2f}", "GH/s"
            else: return f"{val/1000:.2f}", "GH/s"
        elif algo == "Equihash": 
            if "Pro" in current_model or "Z15+" in current_model: return f"{val:.2f}", "kSol/s"
            return f"{val/1000:.2f}", "kSol/s"
        elif algo == "X11": return f"{val:.2f}", "GH/s"
        elif algo == "Etchash": return f"{val:.2f}", "MH/s"
        elif algo == "kHeavyHash": return f"{val/1000:.2f}", "TH/s"
        else: return f"{val:.2f}", "H/s"

    final_real_val, u_r = format_hr(r_val, final_algo, final_model)
    final_avg_val, u_a = format_hr(a_val, final_algo, final_model)

    # === 4. КУЛЕРЫ И ТЕМПЕРАТУРЫ ===
    fans = []
    for i in range(1, 9):
        f = flat_data.get(f'fan{i}')
        if f and str(f).isdigit() and int(f) > 0:
            fans.append(str(f))

    temps = []
    for i in range(1, 9):
        t = flat_data.get(f'temp2_{i}')
        if not t: t = flat_data.get(f'temp_chip{i}')
        if not t: t = flat_data.get(f'temp{i}')
        
        # ДОБАВЛЯЕМ ПРОВЕРКУ: значение должно быть числом и больше 0
        try:
            if t is not None and float(str(t)) > 0:
                if isinstance(t, str) and '-' in t:
                    t_vals = [int(x) for x in t.split('-') if x.isdigit()]
                    if t_vals: temps.append(str(max(t_vals)))
                else:
                    temps.append(str(int(float(t)))) # Округляем до целого для красоты
        except:
            pass

    # === 5. НОМЕРА СЛОМАННЫХ ПЛАТ ===
    has_hw_error = False
    failed_boards = [] 
    error_str = ""
    error_details = ""
    
    for i in range(1, 9):
        chain_key = f"chain_acs{i}"
        if chain_key in flat_data:
            # Отвалившиеся чипы могут быть 'x' или '-'
            val_str = str(flat_data[chain_key]).lower()
            if 'x' in val_str or '-' in val_str:
                has_hw_error = True
                failed_boards.append(str(i)) 

    if has_hw_error:
        boards_str = ",".join(failed_boards) 
        error_str = f"HW ERR (B{boards_str})" 
        error_details = f"Сгоревшие или отвалившиеся чипы ('x', '-') на плате {boards_str}"

    # === 6. ОПРОС ПОРТА 6060 (ПРИОРИТЕТ НАД СНОМ) ===
    short_6060_err, detail_6060_err = diagnostics
    
    if short_6060_err:
        has_hw_error = True
        if error_str:
            error_str = f"{error_str} + {short_6060_err}"
            error_details = f"{error_details}\n{detail_6060_err}"
        else:
            error_str = short_6060_err
            error_details = detail_6060_err

    # Если есть аппаратная ошибка (плата или порт 6060), то устройство ТОЧНО не спит!
    if has_hw_error and work_mode is None:
        is_sleeping = False

    # === 7. ЖЕСТКАЯ ЛОГИКА СТАТУСОВ ===
    if is_sleeping:
        status = "Sleep"
        error_str = ""
        error_details = ""
    elif float(r_val) > 0.0:
        status = "Running"
    else:
        status = "Error"
        if not error_str:
            error_str = "NO HASH"
            error_details = "Устройство не спит, но хешрейт равен 0."

    try: raw_h = float(str(final_real_val).replace(',', '.').strip())
    except: raw_h = 0.0

    return {
        "IP": ip, 
        "Make": "Bitmain", 
        "Model": final_model, 
        "Algo": final_algo, 
        "Status": status, 
        "Uptime": get_uptime_str(uptime_sec),
        "Real": f"{raw_h} {u_r}", 
        "Avg": f"{final_avg_val} {u_a}",
        "Fan": " ".join(fans),
        "Temp": " ".join(temps), 
        "Pool": pool, 
        "Worker": worker,
        "SortIP": int(ipaddress.IPv4Address(ip)),
        "RawHash": float(r_val),
        "Error": error_str, 
        "ErrorDetails": error_details
    }
