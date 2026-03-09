import socket
import json
import time
import csv
import sys
import os
import toml
import gspread
from concurrent.futures import ThreadPoolExecutor

# --- НАСТРОЙКИ ---
MAX_RETRIES = 2       
TIMEOUT = 2.0         
MAX_THREADS = 50      
PORTS_TO_CHECK = [4028, 4433, 8889, 80]

SECRETS_FILE = "secrets.toml"
CREDENTIALS_FILE = "credentials.json"

# --- ИМПОРТ ПАРСЕРА ---
try:
    # Пытаемся подключить "мозги" агента для определения модели
    from miner_scanner.core import process_ip
    SCANNER_LIB = True
except ImportError:
    print("⚠️ Библиотека miner_scanner не найдена. Детальной инфо о моделях не будет.")
    SCANNER_LIB = False

def load_resource(filename):
    if getattr(sys, 'frozen', False):
        app_path = os.path.dirname(sys.executable)
    else:
        app_path = os.path.dirname(os.path.abspath(__file__))
    ext = os.path.join(app_path, filename)
    if os.path.exists(ext): return ext
    if getattr(sys, 'frozen', False):
        int_p = os.path.join(sys._MEIPASS, filename)
        if os.path.exists(int_p): return int_p
    return ext

def get_ips_from_google():
    path = load_resource(SECRETS_FILE)
    if not os.path.exists(path):
        print("❌ secrets.toml не найден")
        return []
    
    try:
        with open(path, "r", encoding="utf-8") as f: conf = toml.load(f)
        sheet_url = conf.get("google", {}).get("sheet_url")
        sheet_name = conf.get("google", {}).get("sheet_tab_name")
        
        cred_path = load_resource(CREDENTIALS_FILE)
        gc = gspread.service_account(filename=cred_path)
        sh = gc.open_by_url(sheet_url)
        ws = sh.worksheet(sheet_name)
        
        raw = ws.get_all_values()
        ips = []
        for r in raw[1:]:
            if len(r) > 0:
                ip = r[0].strip()
                if len(ip) > 7 and "." in ip: ips.append(ip)
        return ips
    except Exception as e:
        print(f"❌ Ошибка Google: {e}")
        return []

def scan_single_device(ip):
    # Результат по умолчанию
    res = {
        "IP": ip,
        "Status": "Dead",
        "Port": "-",
        "Ping (s)": 0.0,
        "Model": "-",
        "Hashrate": "-",
        "Error": ""
    }

    start_t = time.time()
    open_port = None

    # 1. ПРОВЕРКА ПОРТОВ (Ping)
    for port in PORTS_TO_CHECK:
        for attempt in range(1, MAX_RETRIES + 1):
            s = None
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(TIMEOUT)
                s.connect((ip, port))
                
                # Если 80 порт - просто коннект
                if port == 80:
                    open_port = port
                    s.close()
                    break
                
                # Иначе шлем команду
                s.sendall(json.dumps({"command": "version"}).encode('utf-8'))
                data = s.recv(1024)
                if data:
                    open_port = port
                    s.close()
                    break
            except: pass
            finally:
                if s: 
                    try: 
                        s.close() 
                    except: 
                        pass
        
        if open_port: break
    
    ping_time = time.time() - start_t
    res["Ping (s)"] = round(ping_time, 3)

    # 2. ПОЛУЧЕНИЕ ДАННЫХ (Deep Scan)
    if open_port:
        res["Status"] = "Alive"
        res["Port"] = str(open_port)
        
        if SCANNER_LIB:
            try:
                # Используем логику агента для парсинга
                data = process_ip(ip)
                if data:
                    res["Model"] = data.get("Model", "Unknown")
                    # Берем хешрейт из доступных полей
                    res["Hashrate"] = data.get("Real") or data.get("HS_RT") or data.get("hashrate") or "0"
                else:
                    res["Error"] = "Port Open, but Parse Failed"
            except Exception as e:
                res["Error"] = str(e)
    else:
        res["Error"] = "Timeout / Connection Refused"

    return res

def run_diagnostic():
    print("👨‍⚕️ ДИАГНОСТИКА v3 (Full Report)")
    print(f"⚙️ Ports: {PORTS_TO_CHECK} | Threads: {MAX_THREADS}")
    
    ips = get_ips_from_google()
    if not ips: return

    count = len(ips)
    print(f"🎯 Целей: {count}. Начинаю сканирование...")
    
    # ЗАМЕР ОБЩЕГО ВРЕМЕНИ
    global_start = time.time()
    
    results = []
    done = 0
    
    with ThreadPoolExecutor(max_workers=MAX_THREADS) as ex:
        futures = {ex.submit(scan_single_device, ip): ip for ip in ips}
        for f in futures:
            results.append(f.result())
            done += 1
            if done % 10 == 0: print(f"   Прогресс: {done}/{count}")

    global_duration = time.time() - global_start

    # СОХРАНЕНИЕ
    csv_name = "full_scan_report.csv"
    with open(csv_name, 'w', newline='', encoding='utf-8') as f:
        cols = ['IP', 'Status', 'Port', 'Ping (s)', 'Model', 'Hashrate', 'Error']
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(results)
    
    # ИТОГИ
    alive = len([x for x in results if x['Status'] == 'Alive'])
    
    print("\n" + "="*40)
    print(f"✅ СКАНИРОВАНИЕ ЗАВЕРШЕНО")
    print(f"⏱  Общее время: {global_duration:.2f} сек")
    print(f"📊 Живых: {alive} / {count}")
    print(f"📄 Отчет сохранен: {csv_name}")
    print("="*40)

if __name__ == "__main__":
    run_diagnostic()