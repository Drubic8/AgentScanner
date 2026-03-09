import requests
import json
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

TARGET_IP = "192.168.154.183"

# Страницы, о которых вы сказали
URLS = [
    f"http://{TARGET_IP}/cgi-bin/minerStatus.cgi",
    f"http://{TARGET_IP}/cgi-bin/minerConfiguration.cgi"
]

# Стандартные связки логинов и паролей для асиков
AUTHS = [
    ("root", "root"),
    ("root", "admin"),
    ("admin", "admin")
]

def fetch_web_data():
    print(f"📡 Пробуем вытащить данные через WEB (IP: {TARGET_IP})...")
    results = {}
    
    for url in URLS:
        print(f"\n🔗 Проверка: {url}")
        success = False
        
        for user, pwd in AUTHS:
            if success: break
            
            # Пробуем Basic Auth
            try:
                r = requests.get(url, auth=HTTPBasicAuth(user, pwd), timeout=5)
                if r.status_code == 200:
                    print(f"✅ Успех (Basic Auth) с логином {user}:{pwd}")
                    results[url] = r.text
                    success = True
                    continue
            except: pass
            
            # Пробуем Digest Auth (часто используется в Antminer)
            try:
                r = requests.get(url, auth=HTTPDigestAuth(user, pwd), timeout=5)
                if r.status_code == 200:
                    print(f"✅ Успех (Digest Auth) с логином {user}:{pwd}")
                    results[url] = r.text
                    success = True
                    continue
            except: pass
            
        if not success:
            print("❌ Не удалось получить доступ (возможно другой пароль)")
            results[url] = "ERROR or UNAUTHORIZED"
            
    # Сохраняем в файл
    filename = "hammer_web_dump.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=4, ensure_ascii=False)
        
    print(f"\n💾 Готово! Данные сохранены в {filename}")

if __name__ == "__main__":
    fetch_web_data()