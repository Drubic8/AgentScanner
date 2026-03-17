import ipaddress
import requests
from requests.auth import HTTPDigestAuth, HTTPBasicAuth
from ..utils import get_uptime_str, normalize_hashrate

def scan_elphapex(ip, user="root", pwd="root"):
    stats_data = {}
    conf_data = {}
    auth_failed = False

    # 1. Запрашиваем stats.cgi (Хешрейт, кулеры, температуры, ошибки плат)
    try:
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

    if not stats_data:
        return None

    # 2. Запрашиваем get_miner_conf.cgi (Режим сна и пулы)
    try:
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

    # === РАЗБОР ДАННЫХ ИЗ STATS ===
    info = stats_data.get('INFO', {})
    s = stats_data.get('STATS', [{}])[0]

    model_type = info.get('type', 'Unknown')
    model = f"Elphapex {model_type}"

    r_val = float(s.get('rate_5s', 0))
    a_val = float(s.get('rate_avg', 0))
    
    real_s, u_r = normalize_hashrate(r_val, "SCRYPT")
    avg_s, u_a = normalize_hashrate(a_val, "SCRYPT")

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
    if 'pools' in conf_data and len(conf_data['pools']) > 0:
        pool = conf_data['pools'][0].get('url', '').replace("stratum+tcp://", "")
        work = conf_data['pools'][0].get('user', '')

    # === УМНАЯ ЛОГИКА СНА И СТАТУСОВ ===
    status = "Starting"
    work_mode = str(conf_data.get('fc-work-mode', '')).strip()

    # 1. Прямое попадание (прочитали конфиг)
    if work_mode == "-1000":
        status = "Sleep"
        error_str = ""
        error_details = "Устройство в спящем режиме (fc-work-mode: -1000)"
    # 2. Вторичный признак (если конфиг не успел скачаться, но мы видим нули)
    elif uptime_sec == 0 and r_val == 0.0:
        status = "Sleep"
        error_str = ""
        error_details = "Устройство остановлено (uptime: 0, hash: 0)"
    # 3. Неверный пароль во сне
    elif auth_failed and r_val == 0.0:
        status = "Sleep"
        error_str = "Auth Reqd"
        error_details = "Пароль изменен. Не удалось прочитать конфигурацию."
    # 4. Нормальная работа
    elif r_val > 0.0:
        status = "Running"
    # 5. Реальная ошибка (аптайм идет, а хеша нет)
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
        "Status": status,  # <--- ВОТ ОНО!
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