import time
import json
import os
import sys
import toml
import requests
import gspread
import threading
import _thread as thread
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

# --- УТИЛИТА ДЛЯ ПОИСКА ФАЙЛОВ В EXE ---
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

# ==========================================
# 🔧 НАСТРОЙКА ЯДРА СКАНЕРА (TURBO MODE)
# ==========================================
try:
    import miner_scanner.config as config_module
    from miner_scanner.core import process_ip
    
    # 1. Максимум потоков, чтобы "проглатывать" мертвые IP пачками
    config_module.MAX_THREADS = 50 
    
    # 2. Таймаут 2.0 сек (Живые отвечают за 0.01с, этого хватит)
    config_module.TIMEOUT = 2    
    
    # 3. ВСЕГО 1 ПОПЫТКА! 
    # Если асик не ответил за 2 секунды - считаем его мертвым сразу.
    # Это сэкономит кучу времени на 166 мертвых устройствах.
    config_module.RETRY_COUNT = 2    
    
    # 4. Быстрый пинг
    config_module.PING_TIMEOUT = 2  
    
    print(f"🔧 SYSTEM: Config patched (Threads=100, Timeout=2.0s, Retries=1)")
    SCANNER_AVAILABLE = True
except ImportError:
    print("❌ ERROR: miner_scanner lib not found")
    SCANNER_AVAILABLE = False


# КОНСТАНТЫ ФАЙЛОВ
SECRETS_FILE = "secrets.toml"
CREDENTIALS_FILE = "credentials.json"
DEFAULT_SERVER_URL = "https://api.minerhotel-cloud.ru"

# НАСТРОЙКИ АГЕНТА
CYCLE_PAUSE = 30    # Пауза 30 секунд (чтобы чаще обновлять)
BATCH_SIZE = 50     # Отправка на сервер пачками
MAX_WORKERS = 50   # Потоки агента (Синхронизировано с патчем)
GOOGLE_SYNC_RATE = 5 

def get_config():
    path = load_resource(SECRETS_FILE)
    s = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f: s = toml.load(f)
        except Exception as e:
            print(f"⚠️ Ошибка чтения secrets.toml: {e}")
            return None, None, None, None
    
    token = s.get("cloud", {}).get("token")
    url = s.get("cloud", {}).get("url", DEFAULT_SERVER_URL)
    google_conf = s.get("google", {})
    sheet_url = google_conf.get("sheet_url")
    sheet_name = google_conf.get("sheet_tab_name")
    
    return token, url, sheet_url, sheet_name

def scan_worker(target):
    """Сканирует 1 IP с жестким таймаутом"""
    if not SCANNER_AVAILABLE: return None, target['row_id']
    
    # Контейнер для результата, так как мы запускаем во внутреннем потоке
    result_container = []
    
    def _run_scan():
        try:
            res = process_ip(target['ip'])
            if isinstance(res, dict):
                res['Hashrate'] = res.get('Real') or res.get('HS_RT') or res.get('hashrate') or 0
                res.update({
                    'Client': target.get('client'), 
                    'Responsible': target.get('responsible'),
                    'Tariff': target.get('tariff'), 
                    'Consumption': target.get('consumption'), 
                    'Serial_Sheet': target.get('serial')
                })
                result_container.append(res)
        except:
            pass # Игнорируем ошибки сканирования

    # Запускаем _run_scan в отдельном мини-потоке с контролем времени
    scan_thread = threading.Thread(target=_run_scan)
    scan_thread.daemon = True
    scan_thread.start()
    
    # Даем сканеру максимум 10.0 секунды (это ОЧЕНЬ много для локалки)
    scan_thread.join(timeout=45.0) 
    
    if scan_thread.is_alive():
        # Если поток всё еще жив через 3 секунды - это зависание (timeout)
        # Мы бросаем его и возвращаем None (Offline)
        return None, target['row_id']
        
    if result_container:
        return result_container[0], target['row_id']
        
    return None, target['row_id']

# === ГЛАВНАЯ ФУНКЦИЯ ===
def run_agent_cycle():
    print(f"🚀 AGENT v5.1 (Turbo Mode)")
    
    if not SCANNER_AVAILABLE:
        print("🔥 КРИТИЧЕСКАЯ ОШИБКА: Модуль сканирования отсутствует.")
        while True: time.sleep(10)

    token, base_url, sheet_url, sheet_tab_name = get_config()
    
    missing = []
    if not token: missing.append("cloud.token")
    if not sheet_url: missing.append("google.sheet_url")
    if not sheet_tab_name: missing.append("google.sheet_tab_name")

    if missing:
        print(f"⚠️ ОШИБКА КОНФИГУРАЦИИ: {', '.join(missing)}")
        return

    print(f"📡 Server: {base_url}")
    print(f"⚡ Threads: {MAX_WORKERS}")

    cached_targets = []
    last_refresh = 0
    cycle_num = 0

    gc = None; ws = None

    while True:
        try:
            cycle_num += 1
            cycle_start = time.time()
            
            # 1. ОБНОВЛЕНИЕ СПИСКА ИЗ GOOGLE (Раз в 10 минут)
            if time.time() - last_refresh > 600 or not cached_targets:
                print(f"\n[{datetime.now().strftime('%H:%M')}] 📥 Скачиваю список из Google...")
                try:
                    cred_path = load_resource(CREDENTIALS_FILE)
                    if not os.path.exists(cred_path):
                        print(f"❌ Нет файла ключей: {cred_path}")
                        time.sleep(5); continue

                    if not gc: gc = gspread.service_account(filename=cred_path)
                    sh = gc.open_by_url(sheet_url)
                    ws = sh.worksheet(sheet_tab_name)
                    
                    raw = ws.get_all_values()
                    new_t = []
                    # Парсинг таблицы
                    for i, r in enumerate(raw[1:], start=2):
                        def g(x): return r[x] if len(r)>x else ""
                        ip = g(0).strip()
                        if len(ip) > 7:
                            new_t.append({
                                "row_id": i, 
                                "ip": ip, 
                                "serial": g(1), 
                                "client": g(2), 
                                "tariff": g(3),
                                "responsible": g(5),
                                "consumption": g(6)
                            })
                    cached_targets = new_t
                    last_refresh = time.time()
                    print(f"✅ Список обновлен: {len(cached_targets)} устройств")
                except Exception as e:
                    print(f"⚠️ Ошибка Google API: {e}")
                    if not cached_targets: time.sleep(10); continue

            # 2. СКАНИРОВАНИЕ
            total_devs = len(cached_targets)
            total_online = 0
            google_buffer = [] 
            
            print(f"\n🔄 Цикл #{cycle_num}. Сканирую {total_devs} IP...")

            # Разбиваем на большие пачки для скорости
            for i in range(0, total_devs, BATCH_SIZE):
                batch_start_time = time.time() # <--- НАЧИНАЕМ ОТСЧЕТ
                
                batch = cached_targets[i : i + BATCH_SIZE]
                batch_results = []
                
                # ... (здесь ваш код с ThreadPoolExecutor без изменений) ...
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
                    futures = {ex.submit(scan_worker, t): t for t in batch}
                    for f in futures:
                        res, row_id = f.result()
                        
                        cell = f"H{row_id}:L{row_id}"
                        if res:
                            batch_results.append(res)
                            google_buffer.append({"range": cell, "values": [[ "Online", str(res.get("Uptime","-")), str(res.get("Real","0")), str(res.get("Temp","-")), str(res.get("Fan","-")) ]] })
                        else:
                            google_buffer.append({"range": cell, "values": [["Offline", "-", "0", "-", "-"]]})

                # Отправка на сервер
                if batch_results:
                    try:
                        requests.post(f"{base_url}/api/v1/update", json={"token": token, "miners": batch_results}, timeout=10)
                        total_online += len(batch_results)
                    except Exception as e: print(f"❌ Server Error: {e}")

                # === [FIX] ВЫВОД ВРЕМЕНИ И ОПОВЕЩЕНИЕ ===
                batch_duration = time.time() - batch_start_time
                batch_num = i // BATCH_SIZE + 1
                
                # Если пачка сканировалась дольше 10 секунд - выводим предупреждение
                time_str = f"{batch_duration:.1f} сек"
                if batch_duration > 15.0:
                    time_str = f"⚠️ ОЧЕНЬ ДОЛГО ({time_str})"
                    
                print(f"   📡 Пачка {batch_num}: обработано {len(batch)} IP за {time_str}")

            # 3. СИНХРОНИЗАЦИЯ С GOOGLE (Раз в 5 циклов)
            if cycle_num % GOOGLE_SYNC_RATE == 0:
                print(f"💾 Синхронизация статусов в Google...")
                try:
                    if ws: ws.batch_update(google_buffer)
                    print("✅ Google OK")
                except Exception as e: print(f"⚠️ Google Write Error: {e}")

            duration = time.time() - cycle_start
            print(f"🏁 Круг завершен за {duration:.1f} сек. Онлайн: {total_online}/{total_devs}")
            time.sleep(CYCLE_PAUSE)

        except KeyboardInterrupt: break
        except Exception as e:
            print(f"🔥 CRASH: {e}")
            time.sleep(5)

if __name__ == "__main__":
    run_agent_cycle()