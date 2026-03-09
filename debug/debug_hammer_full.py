import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth
import re
import json

TARGET_IP = "192.168.154.183"
USER = "root"
PWD = "root"

def fetch_page(url, user, pwd):
    """Безопасная загрузка страницы с поддержкой авторизации"""
    try:
        r = requests.get(url, auth=HTTPBasicAuth(user, pwd), timeout=5)
        if r.status_code == 401:
            r = requests.get(url, auth=HTTPDigestAuth(user, pwd), timeout=5)
        if r.status_code == 200:
            return r.text
    except Exception as e:
        print(f"Ошибка загрузки {url}: {e}")
    return None

def test_hammer_full():
    print(f"📡 Подключаюсь к Hammer D10 (IP: {TARGET_IP})...")
    
    status_url = f"http://{TARGET_IP}/cgi-bin/minerStatus.cgi"
    config_url = f"http://{TARGET_IP}/cgi-bin/minerConfiguration.cgi"
    
    html_status = fetch_page(status_url, USER, PWD)
    html_config = fetch_page(config_url, USER, PWD)
    
    if not html_status or not html_config:
        print("❌ Не удалось получить страницы. Проверьте IP и пароль.")
        return

    # ==========================================
    # ИЗВЛЕКАЕМ ДАННЫЕ
    # ==========================================
    data = {
        "Model": "Hammer D10",
        "Uptime": "0",
        "Algo": "Unknown",
        "Real_HR": 0.0,
        "Avg_HR": 0.0,
        "Temp_Max": 0,
        "Fan_Max": 0,
        "Pool": "",
        "Worker": ""
    }

    # --- 1. ПАРСИМ СТАТУС (html_status) ---
    # Uptime
    m_uptime = re.search(r'<cite id="bb_elapsed">(.*?)</cite>', html_status)
    if m_uptime: data["Uptime"] = m_uptime.group(1).strip()
    
    # Real HR
    m_real = re.search(r'<cite id="bb_ghs5s">([\d\,\.]+)</cite>', html_status)
    if m_real: data["Real_HR"] = float(m_real.group(1).replace(',', ''))
    
    # Avg HR
    m_avg = re.search(r'<cite id="bb_ghsav">([\d\,\.]+)</cite>', html_status)
    if m_avg: data["Avg_HR"] = float(m_avg.group(1).replace(',', ''))
    
    # Fans
    fans = re.findall(r'<td id="bb_fan\d+".*?>([\d\,\.]+)</td>', html_status)
    fans_clean = [int(f.replace(',', '')) for f in fans if f.strip() and f != '0']
    if fans_clean: data["Fan_Max"] = max(fans_clean)
    
    # Temps
    temps = re.findall(r'<div id="cbi-table-1-temp2?">([\d\,\.\s]+)</div>', html_status)
    all_temps = []
    for t_str in temps:
        all_temps.extend([int(t.strip()) for t in t_str.split(',') if t.strip().isdigit()])
    if all_temps: data["Temp_Max"] = max(all_temps)


    # --- 2. ПАРСИМ КОНФИГУРАЦИЮ (html_config) ---
    # Ищем блок bb_data_arr
    json_match = re.search(r'bb_data_arr\s*=\s*(\[.*?\]);', html_config, re.DOTALL)
    if json_match:
        try:
            config_json = json.loads(json_match.group(1))[0]
            
            # Извлекаем Алгоритм (coin-type)
            coin_type = config_json.get("coin-type", "").lower()
            if coin_type == "ltc":
                data["Algo"] = "Scrypt" # LTC = Scrypt алгоритм
            else:
                data["Algo"] = coin_type.upper()
                
            # Извлекаем Пулы
            if "pools" in config_json and len(config_json["pools"]) > 0:
                main_pool = config_json["pools"][0]
                data["Pool"] = main_pool.get("url", "")
                data["Worker"] = main_pool.get("user", "")
                
        except Exception as e:
            print(f"Ошибка парсинга JSON конфига: {e}")

    # ==========================================
    # ВЫВОД РЕЗУЛЬТАТОВ
    # ==========================================
    print("\n✅ ДАННЫЕ УСПЕШНО ПОЛУЧЕНЫ:\n")
    print(f" 🔹 Model   : {data['Model']}")
    print(f" 🔹 Algo    : {data['Algo']}")
    print(f" 🔹 Uptime  : {data['Uptime']}")
    print("-" * 30)
    print(f" 🔹 Real HR : {data['Real_HR']} MH/s")
    print(f" 🔹 Avg HR  : {data['Avg_HR']} MH/s")
    print("-" * 30)
    print(f" 🔹 Temp Max: {data['Temp_Max']} °C")
    print(f" 🔹 Fan Max : {data['Fan_Max']} RPM")
    print("-" * 30)
    print(f" 🔹 Pool    : {data['Pool']}")
    print(f" 🔹 Worker  : {data['Worker']}")
    print("\n" + "="*40)

if __name__ == "__main__":
    test_hammer_full()