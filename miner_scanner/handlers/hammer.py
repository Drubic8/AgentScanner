import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth
import re
import json

def parse_hammer(ip, user="root", pwd="root"):
    status_url = f"http://{ip}/cgi-bin/minerStatus.cgi"
    config_url = f"http://{ip}/cgi-bin/minerConfiguration.cgi"
    
    result = {
        "ip": ip,
        "make": "Hammer",
        "model": "Hammer D10",
        "algo": "Scrypt", 
        "hashrate": 0.0,
        "temp_max": 0,
        "fan_max": 0,
        "pool": "",
        "worker": "",
        "uptime": "0",
        "status": "Online" # Если дойдем до конца, значит онлайн
    }

    try:
        r_status = requests.get(status_url, auth=HTTPBasicAuth(user, pwd), timeout=5)
        if r_status.status_code == 401:
            r_status = requests.get(status_url, auth=HTTPDigestAuth(user, pwd), timeout=5)
        
        # Если страница не открылась - это не наш клиент
        if r_status.status_code != 200:
            return None
            
        html_status = r_status.text
        
        # === ГЛАВНАЯ ПРОВЕРКА НА HAMMER ===
        # Если на странице нет тега bb_elapsed, значит это вообще не этот асик (а например, роутер)
        m_uptime = re.search(r'<cite id="bb_elapsed">(.*?)</cite>', html_status)
        if not m_uptime:
            return None # Отбрасываем, это не Hammer!
            
        result["uptime"] = m_uptime.group(1).strip()
        
        # Парсим Хешрейт
        m_real = re.search(r'<cite id="bb_ghs5s">([\d\,\.]+)</cite>', html_status)
        if m_real: 
            result["hashrate"] = float(m_real.group(1).replace(',', ''))
        
        # Парсим Кулеры
        fans = re.findall(r'<td id="bb_fan\d+".*?>([\d\,\.]+)</td>', html_status)
        fans_clean = [int(f.replace(',', '')) for f in fans if f.strip() and f != '0']
        if fans_clean: result["fan_max"] = max(fans_clean)
        
        # Парсим Температуру
        temps = re.findall(r'<div id="cbi-table-1-temp2?">([\d\,\.\s]+)</div>', html_status)
        all_temps = []
        for t_str in temps:
            all_temps.extend([int(t.strip()) for t in t_str.split(',') if t.strip().isdigit()])
        if all_temps: result["temp_max"] = max(all_temps)

        # 2. Загружаем конфиг (Пулы)
        r_config = requests.get(config_url, auth=HTTPBasicAuth(user, pwd), timeout=5)
        if r_config.status_code == 401:
            r_config = requests.get(config_url, auth=HTTPDigestAuth(user, pwd), timeout=5)
            
        if r_config.status_code == 200:
            html_config = r_config.text
            json_match = re.search(r'bb_data_arr\s*=\s*(\[.*?\]);', html_config, re.DOTALL)
            if json_match:
                config_json = json.loads(json_match.group(1))[0]
                coin_type = config_json.get("coin-type", "").lower()
                if coin_type == "ltc": result["algo"] = "Scrypt"
                elif coin_type: result["algo"] = coin_type.upper()
                    
                if "pools" in config_json and len(config_json["pools"]) > 0:
                    main_pool = config_json["pools"][0]
                    result["pool"] = main_pool.get("url", "")
                    result["worker"] = main_pool.get("user", "")

        return result # Успешно спарсили Hammer!

    except Exception:
        return None # При любой ошибке соединения возвращаем None