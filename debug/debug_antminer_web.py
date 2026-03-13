import requests
from requests.auth import HTTPDigestAuth
import json

IP = "192.168.134.13" # IP вашего спящего Antminer
USER = "root"
PWD = "root"

print(f"--- Пробуем достать конфиг и статус СНА из {IP} ---")
url = f"http://{IP}/cgi-bin/get_miner_conf.cgi"

try:
    resp = requests.get(url, auth=HTTPDigestAuth(USER, PWD), timeout=5)
    if resp.status_code == 200:
        data = resp.json()
        print("УСПЕХ! JSON получен. Ищем режим работы:")
        
        mode = data.get("bitmain-work-mode")
        print(f"\nПоле 'bitmain-work-mode': {mode}")
        if str(mode) == "1":
            print("Расшифровка: АСИК В РЕЖИМЕ СНА (Sleep Mode)!")
        elif str(mode) == "0":
            print("Расшифровка: АСИК В НОРМАЛЬНОМ РЕЖИМЕ (Normal)!")
        else:
            print("Неизвестный режим.")
            
        print("\nПолный ответ:")
        print(json.dumps(data, indent=2))
    else:
        print(f"Ошибка HTTP: {resp.status_code}")
except Exception as e:
    print(f"Ошибка: {e}")