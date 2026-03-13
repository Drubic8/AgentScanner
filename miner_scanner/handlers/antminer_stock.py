import ipaddress
import json
import re
import requests
from requests.auth import HTTPDigestAuth
from ..utils import get_uptime_str, normalize_hashrate

# Импортируем наш новый справочник ошибок (если файл есть)
try:
    from .antminer_dict import get_antminer_error_desc
except ImportError:
    def get_antminer_error_desc(code): return ""

# Функция для глубокого сканирования по WEB API
def get_web_status(ip):
    web_status = None
    short_errors = []
    detailed_errors = []
    
    try:
        # 1. Проверяем режим Сна
        try:
            conf_resp = requests.get(f"http://{ip}/cgi-bin/get_miner_conf.cgi", auth=HTTPDigestAuth("root", "root"), timeout=2)
            if conf_resp.status_code == 200:
                if str(conf_resp.json().get("bitmain-work-mode")) == "1":
                    return "WaitWork", "", ""
        except:
            pass
            
        # 2. Ищем скрытые ошибки
        try:
            sum_resp = requests.get(f"http://{ip}/cgi-bin/summary.cgi", auth=HTTPDigestAuth("root", "root"), timeout=2)
            if sum_resp.status_code == 200:
                status_array = sum_resp.json().get("SUMMARY", [{}])[0].get("status", [])
                
                for item in status_array:
                    if str(item.get("status")).lower() != "s":
                        msg = item.get("msg", "").strip()
                        e_type = str(item.get("type", "unknown")).upper()
                        
                        if msg:
                            # Пытаемся вытащить фирменный код Bitmain (например F040, E112)
                            match = re.search(r'([F|E]\d{3,4})', msg)
                            if match:
                                short_code = match.group(1)
                                dict_desc = get_antminer_error_desc(short_code)
                                # Если есть расшифровка в словаре - добавляем её
                                if dict_desc:
                                    detailed_errors.append(f"[{short_code}] {dict_desc} ({msg})")
                                else:
                                    detailed_errors.append(f"[{short_code}] {msg}")
                            else:
                                short_code = f"{e_type[:4]} ERR"
                                detailed_errors.append(f"[{short_code}] {msg}")
                                
                            short_errors.append(short_code)
        except:
            pass
            
    except Exception:
        pass 
        
    return web_status, " + ".join(short_errors), "\n".join(detailed_errors)


def parse_antminer_stock(ip, resp):
    summary_block = {}
    if resp.get("summary", {}).get('SUMMARY'):
        summary_block = resp["summary"]['SUMMARY'][0]

    flat_data = {}
    if resp.get("stats", {}).get('STATS'):
        for item in resp["stats"]['STATS']:
            if isinstance(item, dict):
                flat_data.update(item)

    # === 1. ОПРЕДЕЛЯЕМ МОДЕЛЬ ===
    raw_type = flat_data.get('Type', summary_block.get('Type', ''))
    model = str(raw_type).replace("Antminer", "").strip()
    final_model = f"Antminer {model}" if model else "Antminer Unknown"

    # === 2. ЛОГИКА АЛГОРИТМОВ ===
    api_algo = flat_data.get('algo', summary_block.get('algo', ''))
    final_algo = "SHA-256"
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
        m_upper = final_model.upper()
        if any(x in m_upper for x in ["L3", "L7", "L9"]): final_algo = "Scrypt"; algo_unit = "SCRYPT"
        elif "D9" in m_upper or "D7" in m_upper: final_algo = "X11"; algo_unit = "X11"
        elif "E9" in m_upper: final_algo = "Etchash"; algo_unit = "M"
        elif "KS" in m_upper: final_algo = "kHeavyHash"
        elif "K7" in m_upper: final_algo = "Eaglesong"; algo_unit = "EAGLESONG"
        elif any(x in m_upper for x in ["S19", "S21", "T21", "T19", "S9"]): final_algo = "SHA-256"; algo_unit = "SHA"

    # === 3. БАЗОВЫЕ ДАННЫЕ ===
    r_val = summary_block.get('GHS 5s', summary_block.get('MHS 5s', 0))
    a_val = summary_block.get('GHS av', summary_block.get('MHS av', 0))
    uptime_sec = int(summary_block.get('Elapsed', flat_data.get('Elapsed', 0)))

    pool, worker = "", ""
    pools_disabled = False
    if resp.get("pools", {}).get('POOLS'):
        pools_list = resp["pools"]['POOLS']
        if pools_list:
            p = pools_list[0]
            pool = p.get('URL', ''); worker = p.get('User', '')
            if str(p.get('Status', '')).lower() == "disabled":
                pools_disabled = True

    final_real, u_r = normalize_hashrate(r_val, algo_unit)
    final_avg, u_a = normalize_hashrate(a_val, algo_unit)

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
        
        if t:
            if isinstance(t, str) and '-' in t:
                try:
                    t_vals = [int(x) for x in t.split('-') if x.isdigit()]
                    if t_vals: temps.append(str(max(t_vals)))
                except: pass
            else:
                temps.append(str(t))

    # === 5. СТАТУС И НОМЕРА СЛОМАННЫХ ПЛАТ ===
    has_hw_error = False
    failed_boards = [] 
    error_str = ""
    error_details = ""
    
    for i in range(1, 9):
        chain_key = f"chain_acs{i}"
        if chain_key in flat_data:
            if 'x' in str(flat_data[chain_key]).lower():
                has_hw_error = True
                failed_boards.append(str(i)) 

    miner_ver = flat_data.get('Miner', 'unknown')

    # Сначала просто записываем ошибку железа в колонку (если она есть)
    if has_hw_error:
        boards_str = ",".join(failed_boards) 
        error_str = f"HW ERR (B{boards_str})" 
        error_details = f"Сгоревшие чипы (крестики 'x') на плате {boards_str}"

    # ТЕПЕРЬ ОПРЕДЕЛЯЕМ ГЛАВНЫЙ СТАТУС (Running, WaitWork, Error, Starting)
    if float(r_val) > 0.0:
        status = "Running" # <--- ЕСЛИ ЕСТЬ ХЕШРЕЙТ, ТО ОН RUNNING (даже с ошибками!)
    else:
        # Если хешрейт 0
        if miner_ver == "" or 'fan_num' not in flat_data or pools_disabled or uptime_sec <= 1:
            status = "WaitWork"
            error_str = "" # Стираем ложные ошибки, если он просто спит
            error_details = ""
        elif uptime_sec > 900:
            status = "Error"
            if not error_str:
                error_str = "NO HASH"
                error_details = "Хешрейт равен 0 более 15 минут. Возможен сбой блока питания."
        else:
            status = "Starting"

    # === 6. ГЛУБОКИЙ ВЕБ-СКАН (Только если хешрейт 0) ===
    if float(r_val) == 0.0:
        web_status, short_web_err, detail_web_err = get_web_status(ip)
        
        if web_status == "WaitWork":
            status = "WaitWork"
            error_str = ""
            error_details = ""
        elif short_web_err:
            status = "Error"
            # Если уже была ошибка (например, крестики), объединяем их
            if error_str and error_str != "NO HASH":
                error_str = f"{error_str} + {short_web_err}"
                error_details = f"{error_details}\n{detail_web_err}"
            else:
                error_str = short_web_err
                error_details = detail_web_err

    # Фикс запятой для сортировки
    try: raw_h = float(str(final_real).replace(',', '.').strip())
    except: raw_h = 0.0

    return {
        "IP": ip, 
        "Make": "Bitmain", 
        "Model": final_model, 
        "Algo": final_algo, 
        "Status": status, 
        "Uptime": get_uptime_str(uptime_sec),
        "Real": f"{raw_h} {u_r}", 
        "Avg": f"{final_avg} {u_a}", 
        "Fan": " ".join(fans),
        "Temp": " ".join(temps), 
        "Pool": pool, 
        "Worker": worker,
        "SortIP": int(ipaddress.IPv4Address(ip)),
        "RawHash": float(r_val),
        "Error": error_str, 
        "ErrorDetails": error_details
    }