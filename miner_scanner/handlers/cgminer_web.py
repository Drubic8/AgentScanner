import requests
import ipaddress
from requests.auth import HTTPBasicAuth, HTTPDigestAuth
import re
import json
from ..utils import get_uptime_str

def parse_cgminer_web(ip, user="root", pwd="root"):
    """
    Универсальный парсер для старых веб-интерфейсов на базе CGminer
    (Hammer D10, Bluestar L1 и другие подобные).
    """
    info_url = f"http://{ip}/cgi-bin/get_system_info.cgi"
    status_url = f"http://{ip}/cgi-bin/minerStatus.cgi"
    config_url = f"http://{ip}/cgi-bin/minerConfiguration.cgi"
    
    try:
        # === 1. ГЛАВНАЯ ПРОВЕРКА НА СОВМЕСТИМОСТЬ ===
        r_status = requests.get(status_url, auth=HTTPBasicAuth(user, pwd), timeout=5)
        if r_status.status_code == 401:
            r_status = requests.get(status_url, auth=HTTPDigestAuth(user, pwd), timeout=5)
        
        if r_status.status_code != 200:
            return None
            
        html_status = r_status.text
        m_uptime = re.search(r'<cite id="bb_elapsed">(.*?)</cite>', html_status)
        if not m_uptime:
            return None # Это не CGminer Web
            
        # Распарсиваем слипшуюся строку вида "1218h35m6s" в секунды
        raw_uptime = m_uptime.group(1).strip()
        total_sec = 0
        
        d_match = re.search(r'(\d+)d', raw_uptime)
        h_match = re.search(r'(\d+)h', raw_uptime)
        m_match = re.search(r'(\d+)m', raw_uptime)
        s_match = re.search(r'(\d+)s', raw_uptime)
        
        if d_match: total_sec += int(d_match.group(1)) * 86400
        if h_match: total_sec += int(h_match.group(1)) * 3600
        if m_match: total_sec += int(m_match.group(1)) * 60
        if s_match: total_sec += int(s_match.group(1))
        
        # Переводим в единый красивый стандарт "Xd Xh Xm"
        uptime_str = get_uptime_str(total_sec) if total_sec > 0 else raw_uptime

        # === 2. ОПРЕДЕЛЯЕМ ПРОИЗВОДИТЕЛЯ И МОДЕЛЬ ===
        make = "CGminer"
        model = "Unknown Web Device"
        
        try:
            # Стучимся в скрытый API за системной информацией
            r_info = requests.get(info_url, auth=HTTPBasicAuth(user, pwd), timeout=3)
            if r_info.status_code == 401:
                r_info = requests.get(info_url, auth=HTTPDigestAuth(user, pwd), timeout=3)
                
            if r_info.status_code == 200:
                info_data = r_info.json()
                miner_type = info_data.get("minertype", "")
                
                if miner_type:
                    model = miner_type # Например: "Bluestar L1" или "Hammer D10"
                    # Берем первое слово как название производителя
                    make = miner_type.split(" ")[0].capitalize() 
        except Exception:
            pass # Если не удалось получить инфо, останутся дефолтные значения

        # === 3. ПАРСИМ СТАТУС (ХЕШРЕЙТ, КУЛЕРЫ, ТЕМПЕРАТУРЫ) ===
        real_hr = 0.0
        avg_hr = 0.0
        m_real = re.search(r'<cite id="bb_ghs5s">([\d\,\.]+)</cite>', html_status)
        if m_real: real_hr = float(m_real.group(1).replace(',', ''))
        
        m_avg = re.search(r'<cite id="bb_ghsav">([\d\,\.]+)</cite>', html_status)
        if m_avg: avg_hr = float(m_avg.group(1).replace(',', ''))
        
        fans = re.findall(r'<td id="bb_fan\d+".*?>([\d\,\.]+)</td>', html_status)
        fans_clean = [f.replace(',', '') for f in fans if f.strip() and f != '0']
        
        temps = re.findall(r'<div id="cbi-table-1-temp2?">([\d\,\.\s]+)</div>', html_status)
        all_temps = []
        for t_str in temps:
            all_temps.extend([t.strip() for t in t_str.split(',') if t.strip().isdigit()])

        # === 4. ПАРСИМ КОНФИГ (ПУЛЫ И АЛГОРИТМ) ===
        pool, work = "", ""
        algo = "Scrypt" # Для Hammer/Bluestar обычно Scrypt
        
        r_config = requests.get(config_url, auth=HTTPBasicAuth(user, pwd), timeout=5)
        if r_config.status_code == 401:
            r_config = requests.get(config_url, auth=HTTPDigestAuth(user, pwd), timeout=5)
            
        if r_config.status_code == 200:
            html_config = r_config.text
            json_match = re.search(r'bb_data_arr\s*=\s*(\[.*?\]);', html_config, re.DOTALL)
            if json_match:
                config_json = json.loads(json_match.group(1))[0]
                coin_type = config_json.get("coin-type", "").lower()
                if coin_type and coin_type != "ltc":
                    algo = coin_type.upper()
                    
                if "pools" in config_json and len(config_json["pools"]) > 0:
                    main_pool = config_json["pools"][0]
                    pool = main_pool.get("url", "").replace("stratum+tcp://", "")
                    work = main_pool.get("user", "")

        # === 5. НОРМАЛИЗАЦИЯ ХЕШРЕЙТА ===
        unit = "MH/s"
        # Переводим MH/s в GH/s для алгоритма Scrypt
        if algo.lower() == "scrypt":
            real_hr = real_hr / 1000.0
            avg_hr = avg_hr / 1000.0
            unit = "GH/s"

        # === ВОЗВРАТ РЕЗУЛЬТАТА ===
        return {
            "IP": ip, 
            "Make": make, 
            "Model": model,
            "Uptime": uptime_str,
            "Real": f"{real_hr:.2f} {unit}", 
            "Avg": f"{avg_hr:.2f} {unit}",
            "Fan": " ".join(fans_clean), 
            "Temp": " ".join(all_temps),
            "Pool": pool, 
            "Worker": work,
            "SortIP": int(ipaddress.IPv4Address(ip)), 
            "Algo": algo,
            "RawHash": real_hr  # Теперь здесь лежит нормализованное значение (GH/s)
        }

    except Exception:
        return None