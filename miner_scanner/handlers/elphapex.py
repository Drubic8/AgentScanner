import ipaddress
import requests
from requests.auth import HTTPDigestAuth, HTTPBasicAuth
from ..utils import get_uptime_str, normalize_hashrate

def scan_elphapex(ip, user="root", pwd="root", port_9588_open=False):
    stats_data = {}
    conf_data = {}
    auth_failed = False

    # 1. Запрашиваем stats.cgi (СНАЧАЛА ЧЕРЕЗ БЭКДОР LUCI)
    try:
        url_stats_luci = f"http://{ip}/cgi-bin/luci/stats.cgi"
        r_stats = requests.get(url_stats_luci, timeout=2.5)
        
        if r_stats.status_code == 200:
            stats_data = r_stats.json()
        else:
            # Если бэкдор закрыт (старая прошивка), идем старым путем с паролями
            url_stats = f"http://{ip}/cgi-bin/stats.cgi"
            r_stats = requests.get(url_stats, timeout=2.5)
            if r_stats.status_code == 401:
                r_stats = requests.get(url_stats, auth=HTTPDigestAuth(user, pwd), timeout=2.5)
                if r_stats.status_code == 401:
                    r_stats = requests.get(url_stats, auth=HTTPBasicAuth(user, pwd), timeout=2.5)
            
            if r_stats.status_code == 200:
                stats_data = r_stats.json()
            elif r_stats.status_code == 401:
                auth_failed = True
    except:
        pass

    # 2. Запрашиваем get_miner_conf.cgi (СНАЧАЛА ЧЕРЕЗ БЭКДОР LUCI)
    try:
        url_conf_luci = f"http://{ip}/cgi-bin/luci/get_miner_conf.cgi"
        r_conf = requests.get(url_conf_luci, timeout=2.5)
        
        if r_conf.status_code == 200:
            conf_data = r_conf.json()
        else:
            url_conf = f"http://{ip}/cgi-bin/get_miner_conf.cgi"
            r_conf = requests.get(url_conf, timeout=2.5)
            if r_conf.status_code == 401:
                r_conf = requests.get(url_conf, auth=HTTPDigestAuth(user, pwd), timeout=2.5)
                if r_conf.status_code == 401:
                    r_conf = requests.get(url_conf, auth=HTTPBasicAuth(user, pwd), timeout=2.5)
            
            if r_conf.status_code == 200:
                conf_data = r_conf.json()
    except:
        pass

    # === 🛡️ ЖЕСТКАЯ ИДЕНТИФИКАЦИЯ (ИДЕЯ С ПОРТОМ И КЛЮЧАМИ) ===
    is_elphapex = port_9588_open

    if not is_elphapex:
        if conf_data and any(k.startswith('fc-') for k in conf_data.keys()):
            is_elphapex = True
        
        if not is_elphapex and stats_data:
            info = stats_data.get('INFO', {})
            m_type = str(info.get('type', '')).upper()
            m_ver = str(info.get('miner_version', '')).upper()
            if 'DG' in m_type or 'ELPHAPEX' in m_type or 'DG' in m_ver:
                is_elphapex = True

    if not is_elphapex:
        return None

    # === РАЗБОР ДАННЫХ ИЗ STATS ===
    info = stats_data.get('INFO', {})
    s = stats_data.get('STATS', [{}])[0]
    
    # 🛡️ ЖЕСТКАЯ ЗАЩИТА ОТ ANTMINER 
    if 'minertype' in info and 'type' not in info:
        return None  # Это 100% Antminer, мгновенно отбрасываем!
        
    model_type = info.get('type', 'Unknown')
    model = f"Elphapex {model_type}"

    # === 🛠 МАТЕМАТИКА ХЕШРЕЙТА (ПОЛНЫЙ ФИКС) ===
    # Собираем реальный хешрейт, складывая параметр 'hashrate' с каждой платы
    r_val = 0.0
    a_val = float(s.get('rate_avg', 0))

    if 'chain' in s and isinstance(s['chain'], list):
        for c in s['chain']:
            # Берем 'hashrate', если его нет - резервный 'rate_real'
            board_hr = float(c.get('hashrate', c.get('rate_real', 0)))
            r_val += board_hr

    # Если платы зависли и вернули нули, берем rate_5s как самый последний шанс,
    # но только если он не космически сломан (> 50000)
    if r_val == 0.0:
        fallback_hr = float(s.get('rate_5s', 0))
        if fallback_hr < 50000:
            r_val = fallback_hr

    # Elphapex отдает данные в MH/s. Переводим в GH/s
    def format_scrypt(val):
        if val == 0: return "0.00", "GH/s"
        if val < 500: return f"{val:.2f}", "GH/s"
        return f"{val/1000:.2f}", "GH/s"

    real_s, u_r = format_scrypt(r_val)
    avg_s, u_a = format_scrypt(a_val)

    uptime_sec = int(s.get('elapsed', 0))

    fans = [str(f) for f in s.get('fan', [])]

    temps = []
    has_hw_error = False
    failed_boards = []

    if 'chain' in s:
        for c in s['chain']:
            def clean_temp(val):
                try:
                    v = float(val)
                    if v > 200: return v / 1000
                    return v
                except: return 0

            val_final = 0
            t_chip_raw = c.get('temp_chip')
            if isinstance(t_chip_raw, list):
                chips = [clean_temp(x) for x in t_chip_raw if x]
                if chips: val_final = max(chips)
            elif t_chip_raw:
                val_final = clean_temp(t_chip_raw)

            if val_final == 0:
                t_pcb_raw = c.get('temp_pcb')
                if isinstance(t_pcb_raw, list):
                    pcbs = [clean_temp(x) for x in t_pcb_raw if x]
                    if pcbs: val_final = max(pcbs)
                elif t_pcb_raw:
                    val_final = clean_temp(t_pcb_raw)

            if val_final > 0:
                temps.append(str(int(val_final)))

            asic_status = str(c.get('asic', '')).lower()
            if 'x' in asic_status or '-' in asic_status:
                has_hw_error = True
                board_idx = c.get('index', '?')
                failed_boards.append(str(board_idx))

    error_str = ""
    error_details = ""
    if has_hw_error:
        b_str = ",".join(failed_boards)
        error_str = f"HW ERR (B{b_str})"
        error_details = f"Сгоревшие или отвалившиеся чипы ('x' или '-') на плате {b_str}"

    pool, work = "", ""
    if conf_data and 'pools' in conf_data and len(conf_data['pools']) > 0:
        pool = conf_data['pools'][0].get('url', '').replace("stratum+tcp://", "")
        work = conf_data['pools'][0].get('user', '')

    # === УМНАЯ ЛОГИКА СНА И СТАТУСОВ ===
    status = "Starting"
    work_mode = ""
    if conf_data:
        work_mode = str(conf_data.get('fc-work-mode', '')).strip()

    if work_mode == "-1000":
        status = "Sleep"
        error_str = ""
        error_details = "Устройство в спящем режиме (fc-work-mode: -1000)"
    elif uptime_sec == 0 and r_val == 0.0:
        status = "Sleep"
        error_str = ""
        error_details = "Устройство остановлено (uptime: 0, hash: 0)"
    elif auth_failed and r_val == 0.0:
        status = "Sleep"
        error_str = "Auth Reqd"
        error_details = "Пароль изменен. Не удалось прочитать конфигурацию."
    elif r_val > 0.0:
        status = "Running"
    else:
        status = "Error"
        if not error_str:
            error_str = "NO HASH"
            error_details = "Устройство не спит, но хешрейт равен 0."

    try: raw_h = float(str(real_s).replace(',', '.').strip())
    except: raw_h = 0.0

    return {
        "IP": ip, 
        "Make": "Elphapex", 
        "Model": model,
        "Status": status,
        "Uptime": get_uptime_str(uptime_sec),
        "Real": f"{raw_h} {u_r}", 
        "Avg": f"{avg_s} {u_a}",
        "Fan": " ".join(fans), 
        "Temp": " ".join(temps),
        "Pool": pool, 
        "Worker": work,
        "SortIP": int(ipaddress.IPv4Address(ip)), 
        "Algo": "Scrypt", 
        "RawHash": raw_h,
        "Error": error_str,
        "ErrorDetails": error_details
    }