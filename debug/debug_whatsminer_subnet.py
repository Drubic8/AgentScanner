import json
import socket
import struct
import concurrent.futures

# Подсеть для сканирования
SUBNET = "192.168.90.2-255"
PORT = 4433
TIMEOUT = 3 # Быстрый таймаут для массового скана

def send_cmd(ip, cmd, param=None):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(TIMEOUT)
        s.connect((ip, PORT))
        
        payload = {"cmd": cmd}
        if param: payload["param"] = param
            
        json_str = json.dumps(payload)
        pkg = struct.pack('<I', len(json_str)) + json_str.encode('utf-8')
        s.sendall(pkg)
        
        len_data = s.recv(4)
        if not len_data or len(len_data) < 4: return None
        pkg_len = struct.unpack('<I', len_data)[0]
        
        if pkg_len > 1000000: return None
        
        chunks = []
        bytes_recd = 0
        while bytes_recd < pkg_len:
            chunk = s.recv(min(pkg_len - bytes_recd, 4096))
            if not chunk: break
            chunks.append(chunk)
            bytes_recd += len(chunk)
            
        return json.loads(b''.join(chunks).decode('utf-8', errors='ignore'))
    except:
        return None
    finally:
        try: s.close()
        except: pass

def safe_float(val):
    try:
        if val is not None and str(val).strip() != "":
            return float(val)
    except: pass
    return 0.0

def check_whatsminer(ip):
    # 1. Запрашиваем инфо
    info = send_cmd(ip, "get.device.info")
    if not info or info.get('code') != 0:
        return None # Устройство выключено или это не Whatsminer

    msg = info.get('msg', {})
    if not isinstance(msg, dict): return None
        
    # Читаем working
    working_status = str(msg.get('miner', {}).get('working', 'unknown')).lower()
    
    # Проверяем наличие реальных ошибок
    err = msg.get('error-code') or msg.get('error_code')
    has_error = False
    if err:
        if isinstance(err, list):
            for e in err:
                if isinstance(e, dict):
                    for k in e.keys():
                        if k != 'reason' and str(k) != "0": has_error = True
                elif str(e) != "0": has_error = True
        elif isinstance(err, dict):
            for k in err.keys():
                if k != 'reason' and str(k) != "0": has_error = True
        elif str(err) != "0":
            has_error = True

    # 2. Запрашиваем хешрейт
    summary_resp = send_cmd(ip, "get.miner.status", "summary")
    raw_hash = 0.0
    
    if summary_resp and summary_resp.get('code') == 0:
        s_msg = summary_resp.get('msg', {})
        if isinstance(s_msg, dict):
            summary = s_msg.get('summary') if isinstance(s_msg.get('summary'), dict) else s_msg
            if isinstance(summary, dict):
                raw_hash = safe_float(summary.get('hash-realtime') or summary.get('HS RT') or summary.get('GHS 5s'))

    # 3. ЛОГИКА СТАТУСОВ
    status = "Unknown"
    if has_error:
        status = "Error"
    elif working_status == "false":
        status = "Sleep"
    elif working_status == "true":
        if raw_hash > 0:
            status = "Running"
        else:
            status = "Starting"

    # Сортировка по последнему октету IP (для красоты)
    ip_last = int(ip.split('.')[-1])
    
    # Формируем красивую строку
    hash_str = f"{raw_hash:.2f}" if raw_hash < 10000 else f"{(raw_hash/1000000):.2f}"
    result_str = f"IP: {ip:<15} | Status: {status:<10} | Hash: {hash_str:<8} | working: {working_status:<5} | Error: {has_error}"
    return (ip_last, result_str)

def get_ips(ip_range_str):
    parts = ip_range_str.split('-')
    start_ip = parts[0]
    end_ip_suffix = parts[1]
    
    start_parts = start_ip.split('.')
    base = '.'.join(start_parts[:3])
    start_num = int(start_parts[3])
    end_num = int(end_ip_suffix)
    
    return [f"{base}.{i}" for i in range(start_num, end_num + 1)]

if __name__ == "__main__":
    ips = get_ips(SUBNET)
    print(f"Начинаем сканирование Whatsminer в подсети {SUBNET} (Всего IP: {len(ips)})\n")
    print("-" * 75)
    
    results = []
    # Запускаем 50 потоков для бешеной скорости
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        futures = {executor.submit(check_whatsminer, ip): ip for ip in ips}
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res:
                results.append(res)
                
    # Сортируем по IP и выводим
    results.sort(key=lambda x: x[0])
    for r in results:
        print(r[1])
        
    print("-" * 75)
    print(f"Сканирование завершено! Найдено Whatsminer: {len(results)}")