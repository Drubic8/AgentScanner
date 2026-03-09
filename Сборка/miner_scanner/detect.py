import socket
import json

def get_miner_make(ip, port=4028):
    """
    Отправляет пробные команды, чтобы понять, это Avalon или Whatsminer.
    Возвращает: "Canaan", "MicroBT" или "Unknown"
    """
    
    # --- 1. ПРОВЕРКА НА AVALON ---
    # Avalon отвечает на команду {"command": "version"}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1) 
        s.connect((ip, port))
        s.sendall(json.dumps({"command": "version"}).encode('utf-8'))
        data = s.recv(4096)
        s.close()
        
        text = data.decode('utf-8', errors='ignore')
        if "Avalon" in text or "Canaan" in text or "PROD" in text:
            return "Canaan"
    except:
        pass 

    # --- 2. ПРОВЕРКА НА WHATSMINER ---
    # Whatsminer отвечает на {"cmd": "get_version"}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        s.connect((ip, port))
        s.sendall(json.dumps({"cmd": "get_version"}).encode('utf-8'))
        data = s.recv(4096)
        s.close()
        
        text = data.decode('utf-8', errors='ignore')
        if "api_ver" in text or "fw_ver" in text:
            return "MicroBT"
    except:
        pass

    return "Unknown"