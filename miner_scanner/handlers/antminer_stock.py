import ipaddress
import json
import re
import requests
from requests.auth import HTTPDigestAuth, HTTPBasicAuth
from ..utils import get_uptime_str

# Функция для опроса порта 6060 (Ошибки)
def get_6060_errors(ip):
    short_errors = []
    detailed_errors = []
    try:
        resp_6060 = requests.get(f"http://{ip}:6060/warning", timeout=2)
        if resp_6060.status_code == 200:
            text = resp_6060.text.strip()
            if text and "searchfailed" not in text:
                parts = [p.strip() for p in text.split(';')]
                if len(parts) >= 2:
                    code = parts[0] 
                    reason = parts[1] 
                    sugg = parts[2] if len(parts) > 2 else "" 
                    short_errors.append(f"ERR [{code}]")
                    desc = f"{reason}. {sugg}".strip()
                    detailed_errors.append(f"[{code}] {desc}")
    except:
        pass
    return " + ".join(short_errors), "\n".join(detailed_errors)

def parse_antminer_stock(ip, resp):
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

    # === БЫСТРАЯ ПРОВЕРКА РЕЖИМА СНА ===
    is_sleeping = False
    
    # Оставляем Mode == 1 только для старых прошивок (S19/T19)
    if flat_data.get('Mode') == 1:
        is_sleeping = True
    elif r_val == 0.0:
        # Если кулеры 0 ИЛИ пулы отключены
        if pools_disabled or flat_data.get('fan_num', 0) == 0:
            # Обходим таймер защиты 60 сек, если вентиляторы И температуры реально на нуле
            if uptime_sec > 60 or pools_disabled or (flat_data.get('fan_num', 0) == 0 and flat_data.get('temp_max', 0) == 0):
                is_sleeping = True

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
    short_6060_err, detail_6060_err = get_6060_errors(ip)
    
    if short_6060_err:
        has_hw_error = True
        if error_str:
            error_str = f"{error_str} + {short_6060_err}"
            error_details = f"{error_details}\n{detail_6060_err}"
        else:
            error_str = short_6060_err
            error_details = detail_6060_err

    # Если есть аппаратная ошибка (плата или порт 6060), то устройство ТОЧНО не спит!
    if has_hw_error:
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

# === НОВЫЙ БЛОК: ФОЛЛБЭК ДЛЯ СПЯЩИХ/ЗАВИСШИХ ANTMINER (ПОРТ 80) ===
def parse_antminer_web_fallback(ip, user="root", pwd="root"):
    sys_info = {}
    conf_info = {}
    auth_failed = False

    try:
        url_sys = f"http://{ip}/cgi-bin/get_system_info.cgi"
        r_sys = requests.get(url_sys, timeout=2)
        if r_sys.status_code == 401:
            r_sys = requests.get(url_sys, auth=HTTPDigestAuth(user, pwd), timeout=2)
            if r_sys.status_code == 401:
                r_sys = requests.get(url_sys, auth=HTTPBasicAuth(user, pwd), timeout=2)
        
        if r_sys.status_code == 200: 
            try: sys_info = r_sys.json()
            except: pass # Если вернулся HTML, sys_info останется пустым
    except: pass

    try:
        url_conf = f"http://{ip}/cgi-bin/get_miner_conf.cgi"
        r_conf = requests.get(url_conf, timeout=2)
        if r_conf.status_code == 401:
            r_conf = requests.get(url_conf, auth=HTTPDigestAuth(user, pwd), timeout=2)
            if r_conf.status_code == 401:
                r_conf = requests.get(url_conf, auth=HTTPBasicAuth(user, pwd), timeout=2)
        
        if r_conf.status_code == 200: 
            try: conf_info = r_conf.json()
            except: pass
        elif r_conf.status_code == 401: 
            auth_failed = True
    except: pass

    if not sys_info and not conf_info: return None

    # === 🛡️ 1. ЖЕСТКАЯ ЗАЩИТА ОТ HAMMER И BLUESTAR ===
    host_name = str(sys_info.get("hostname", "")).lower()
    m_type_lower = str(sys_info.get("minertype", sys_info.get("type", ""))).lower()
    
    if "hammer" in host_name or "hammer" in m_type_lower or "bluestar" in host_name or "bluestar" in m_type_lower:
        return None # Мгновенно отбрасываем!
        
    # === 🛡️ 2. ЖЕСТКАЯ ПОЗИТИВНАЯ ИДЕНТИФИКАЦИЯ BITMAIN ===
    is_bitmain = False
    
    # А) Проверка по уникальным ключам конфига (самый точный метод)
    if conf_info:
        if any(k.startswith('bitmain-') or k.startswith('ant_') for k in conf_info.keys()):
            is_bitmain = True
            
    # Б) Проверка по системным данным (если конфиг закрыт паролем)
    if not is_bitmain and sys_info:
        m_type = str(sys_info.get("minertype", sys_info.get("type", ""))).upper()
        fs_ver = str(sys_info.get("system_filesystem_version", "")).upper()
        h_name = str(sys_info.get("hostname", "")).upper() # <--- Наша находка с hostname
        
        # Если есть прямое упоминание бренда
        if "ANTMINER" in m_type or "BITMAIN" in m_type or "ANTMINER" in fs_ver or "BITMAIN" in fs_ver or "ANTMINER" in h_name:
            is_bitmain = True
        else:
            # Проверяем на популярные префиксы моделей
            ant_prefixes = ["S9", "S11", "S15", "S17", "S19", "S21", "T9", "T11", "T15", "T17", "T19", "T21", "L3", "L7", "L9", "D3", "D5", "D7", "D9", "E3", "E9", "K5", "K7", "KA3", "KS3", "KS5", "Z9", "Z11", "Z15"]
            if any(m_type.startswith(p) for p in ant_prefixes):
                is_bitmain = True

    # Если устройство не смогло доказать, что оно Bitmain - отбрасываем!
    if not is_bitmain:
        return None

    model = sys_info.get("minertype", sys_info.get("type", "Antminer Unknown"))
    
    # === 🛡️ 3. ЗАЩИТА ОТ ELPHAPEX (На всякий случай) ===
    if "DG" in str(model).upper() or "ELPHAPEX" in str(model).upper():
        return None

    uptime_sec = sys_info.get("uptime", 0)
    
    short_6060_err, detail_6060_err = get_6060_errors(ip)

    status = "Error"
    error_str = "API Offline"
    error_details = "Порт 4028 закрыт. Майнинг крашнулся или устройство зависло."
    
    work_mode = conf_info.get("bitmain-work-mode", None)

    if short_6060_err:
        status = "Error"
        error_str = f"API Offline + {short_6060_err}"
        error_details = f"Порт 4028 закрыт. Аппаратная ошибка:\n{detail_6060_err}"
    elif str(work_mode) == "1":
        status = "Sleep"
        error_str = ""
        error_details = "Устройство находится в спящем режиме."
    elif auth_failed:
        status = "Sleep"
        error_str = "Auth Reqd"
        error_details = "Порт 4028 закрыт. Вероятно сон, но стандартный пароль root:root не подошел."

    pool, worker = "", ""
    pools = conf_info.get("pools", [])
    if pools:
        pool = pools[0].get("url", "").replace("stratum+tcp://", "")
        worker = pools[0].get("user", "")

    algo_hint = str(conf_info.get("algo", "")).lower()
    m_upper = model.upper()
    
    final_algo = "SHA-256"
    if algo_hint == "ltc" or any(x in m_upper for x in ["L3", "L7", "L9"]): final_algo = "Scrypt"
    elif any(x in m_upper for x in ["Z11", "Z15"]): final_algo = "Equihash"
    elif "E9" in m_upper: final_algo = "Etchash"
    elif "D9" in m_upper: final_algo = "X11"
    elif "K" in m_upper and "KA3" not in m_upper: final_algo = "kHeavyHash"

    return {
        "IP": ip, 
        "Make": "Bitmain", 
        "Model": model, 
        "Algo": final_algo, 
        "Status": status,
        "Uptime": get_uptime_str(uptime_sec),
        "Real": "0.00", 
        "Avg": "0.00", 
        "Fan": "", 
        "Temp": "", 
        "Pool": pool, 
        "Worker": worker,
        "SortIP": int(ipaddress.IPv4Address(ip)),
        "RawHash": 0.0,
        "Error": error_str, 
        "ErrorDetails": error_details
    }