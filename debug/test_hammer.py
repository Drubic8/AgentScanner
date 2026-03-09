import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth
import re
import json

# ==========================================
# НАСТРОЙКИ ТЕСТЕРА
# ==========================================
TARGET_IP = "192.168.154.183"  # Укажите IP вашего Hammer D10
USER = "root"
PWD = "root"

def test_hammer(ip, user, pwd):
    print(f"🚀 Запуск тестера Hammer D10 (IP: {ip})...")
    
    status_url = f"http://{ip}/cgi-bin/minerStatus.cgi"
    config_url = f"http://{ip}/cgi-bin/minerConfiguration.cgi"
    
    # Функция для безопасного HTTP запроса (с поддержкой Digest/Basic Auth)
    def fetch_page(url):
        try:
            r = requests.get(url, auth=HTTPBasicAuth(user, pwd), timeout=5)
            if r.status_code == 401:
                r = requests.get(url, auth=HTTPDigestAuth(user, pwd), timeout=5)
            if r.status_code == 200:
                return r.text
            else:
                print(f"❌ Ошибка авторизации на {url} (Код: {r.status_code})")
                return None
        except Exception as e:
            print(f"❌ Ошибка соединения с {url}: {e}")
            return None

    # 1. Запрашиваем страницу статуса (эмуляция STATS и SUMMARY)
    print("\n📥 Запрос страницы статуса (Miner Status)...")
    html_status = fetch_page(status_url)
    
    if html_status:
        print("\n=== 📊 SUMMARY (Сводка) ===")
        # Ищем хешрейт
        hr_match = re.search(r'<cite id="bb_ghs5s">([\d\,\.]+)</cite>', html_status)
        if hr_match:
            hr_raw = hr_match.group(1)
            print(f" [✓] Хешрейт (RT): {hr_raw} MH/s")
        else:
            print(" [?] Хешрейт не найден")

        print("\n=== 🌡️ STATS (Оборудование) ===")
        # Ищем кулеры
        fans = re.findall(r'<td id="bb_fan\d+".*?>([\d\,\.]+)</td>', html_status)
        fans_clean = [f for f in fans if f.strip() and f != '0']
        print(f" [✓] Кулеры ({len(fans_clean)} шт): {', '.join(fans_clean)} RPM")
        
        # Ищем температуры (обычно 2 или больше датчиков на плату)
        temps1 = re.findall(r'<div id="cbi-table-1-temp">([\d\,\.\s]+)</div>', html_status)
        temps2 = re.findall(r'<div id="cbi-table-1-temp2">([\d\,\.\s]+)</div>', html_status)
        
        all_temps = []
        if temps1: all_temps.extend([t.strip() for t in temps1[0].split(',')])
        if temps2: all_temps.extend([t.strip() for t in temps2[0].split(',')])
        
        all_temps = [t for t in all_temps if t.isdigit()]
        if all_temps:
            print(f" [✓] Температуры чипов/плат: {', '.join(all_temps)} °C")
            print(f" [✓] Макс. температура: {max([int(t) for t in all_temps])} °C")
        else:
            print(" [?] Температуры не найдены")

    # 2. Запрашиваем страницу конфигурации (эмуляция POOLS)
    print("\n📥 Запрос страницы конфигурации (Miner Configuration)...")
    html_config = fetch_page(config_url)
    
    if html_config:
        print("\n=== 🏊 POOLS (Пулы) ===")
        # Парсим скрытый JSON с пулами внутри HTML
        json_match = re.search(r'bb_data_arr\s*=\s*(\[.*?\]);', html_config, re.DOTALL)
        if json_match:
            try:
                config_data = json.loads(json_match.group(1))
                if config_data and "pools" in config_data[0]:
                    pools = config_data[0]["pools"]
                    for i, p in enumerate(pools):
                        url = p.get('url', 'N/A')
                        worker = p.get('user', 'N/A')
                        print(f" [✓] Пул {i+1}: {url}")
                        print(f"     Воркер: {worker}")
            except Exception as e:
                print(f" [!] Ошибка парсинга пулов: {e}")
        else:
            print(" [?] Конфигурация пулов не найдена в коде страницы")

    print("\n🏁 Тест завершен!")

if __name__ == "__main__":
    test_hammer(TARGET_IP, USER, PWD)