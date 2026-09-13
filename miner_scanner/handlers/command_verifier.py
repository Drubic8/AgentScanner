import requests
import time

def verify_mode(ip, expected_mode_int, auth_basic, auth_digest, retries=3, delay=2):
    """
    Проверяет, реально ли изменился режим работы асика (sleep/normal).
    Делает запрос на чтение конфига, парсит его и сравнивает ожидаемый режим.
    """
    get_url = f"http://{ip}/cgi-bin/get_miner_conf.cgi"
    
    for attempt in range(retries):
        if attempt > 0:
            time.sleep(delay)
            
        try:
            resp = requests.get(get_url, auth=auth_digest, timeout=5)
            if resp.status_code == 401:
                resp = requests.get(get_url, auth=auth_basic, timeout=5)
                
            if resp.status_code == 200:
                try:
                    config = resp.json()
                except Exception:
                    continue
                    
                if not isinstance(config, dict):
                    continue
                    
                current_mode = None
                if "bitmain-work-mode" in config:
                    current_mode = config["bitmain-work-mode"]
                elif "miner-mode" in config:
                    current_mode = config["miner-mode"]
                    
                if current_mode is not None:
                    try:
                        if int(current_mode) == expected_mode_int:
                            return True
                    except (ValueError, TypeError):
                        pass
        except Exception:
            pass
            
    return False
