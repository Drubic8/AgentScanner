import json
import ipaddress
import socket
import struct
from ..utils import get_uptime_str, normalize_hashrate

# === ПРОСТОЙ TCP КЛИЕНТ (ВАШ, БЕЗ ИЗМЕНЕНИЙ) ===
class SimpleWhatsminerTCP:
    # УВЕЛИЧИЛ ТАЙМАУТ ПО УМОЛЧАНИЮ ДО 10 СЕКУНД
    def __init__(self, ip, port, timeout=10):
        self.ip = ip
        self.port = port
        self.timeout = timeout
        self.sock = None

    def connect(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(self.timeout)
            self.sock.connect((self.ip, self.port))
            return True
        except:
            return False

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except: pass

    def send_cmd(self, cmd, param=None):
        if not self.sock: return None
        
        payload = {"cmd": cmd}
        if param:
            payload["param"] = param
            
        json_str = json.dumps(payload)
        # 4 байта длины + JSON
        pkg = struct.pack('<I', len(json_str)) + json_str.encode('utf-8')
        
        try:
            self.sock.sendall(pkg)
            return self._recv_response()
        except:
            return None

    def _recv_response(self):
        try:
            len_data = self.sock.recv(4)
            if not len_data or len(len_data) < 4: return None
            pkg_len = struct.unpack('<I', len_data)[0]
            
            # Защита от мусора
            if pkg_len > 500000: return None

            chunks = []
            bytes_recd = 0
            while bytes_recd < pkg_len:
                chunk = self.sock.recv(min(pkg_len - bytes_recd, 4096))
                if not chunk: break
                chunks.append(chunk)
                bytes_recd += len(chunk)
                
            raw_resp = b''.join(chunks).decode('utf-8', errors='ignore')
            return json.loads(raw_resp)
        except:
            return None

# === ПАРСЕР (ДОРАБОТАННЫЙ) ===
def parse_whatsminer_v3(ip, port=4433):
    # Увеличиваем таймаут при создании подключения
    tcp = SimpleWhatsminerTCP(ip, port, timeout=10)
    if not tcp.connect():
        return None

    # Данные по умолчанию
    model_full = "Whatsminer Detected"
    algo = "SHA-256"
    
    # 1. ЗАПРОС ИНФО (Модель)
    resp_info = tcp.send_cmd("get.device.info")
    if resp_info and resp_info.get('code') == 0:
        info = resp_info.get('msg', {})
        # Ищем модель везде
        m_type = info.get('miner', {}).get('type') or \
                 info.get('sub_version') or \
                 info.get('product_type')
        
        if m_type: model_full = f"Whatsminer {m_type}"
        
        ctype = info.get('miner', {}).get('cointype', 'SHA-256')
        if 'BTC' not in str(ctype).upper(): algo = str(ctype)

    # 2. ЗАПРОС СТАТИСТИКИ (ПРОБУЕМ ВСЕ ВАРИАНТЫ)
    # Сначала "get.miner.status" (основной)
    resp_summary = tcp.send_cmd("get.miner.status", "summary")
    
    # Если ответ пустой или ошибка - пробуем просто "summary" (для новых прошивок)
    if not resp_summary or resp_summary.get('code') != 0:
         # Переподключение иногда нужно для смены контекста команды
         tcp.close()
         tcp.connect()
         resp_summary = tcp.send_cmd("summary")

    # 3. ЗАПРОС ПУЛОВ
    resp_pools = tcp.send_cmd("get.miner.status", "pools")
    if not resp_pools or resp_pools.get('code') != 0:
        resp_pools = tcp.send_cmd("pools")
    
    tcp.close()

    # --- ПАРСИНГ ---
    raw_hash = 0.0
    avg_hash = 0.0
    uptime = 0
    temps = []
    fans = []
    pool = ""
    worker = ""
    
    # A. Parsing Summary (САМОЕ ВАЖНОЕ: ПОИСК КЛЮЧЕЙ)
    if resp_summary:
        msg = resp_summary.get('msg', {})
        # Иногда данные лежат в Msg, а не msg
        if not msg and 'Msg' in resp_summary: msg = resp_summary['Msg']
        
        # Иногда summary внутри msg, иногда msg это и есть summary
        summary = msg.get('summary')
        if not summary: summary = msg 

        if summary and isinstance(summary, dict):
            # 1. ХЕШРЕЙТ (Ищем по всем известным названиям)
            # HS RT = Hashrate Realtime (старые)
            # hash-realtime (новые)
            # GHS 5s (очень старые)
            rt = float(summary.get('hash-realtime') or summary.get('HS RT') or summary.get('GHS 5s', 0)*1000 or 0)
            av = float(summary.get('hash-average') or summary.get('MHS av') or summary.get('GHS av', 0)*1000 or 0)
            
            # API обычно отдает в MH/s. Если число огромное - это MH/s.
            if rt > 10000:
                raw_hash = rt / 1_000_000 # Переводим в TH/s
            else:
                raw_hash = rt # Уже в TH/s

            if av > 10000: avg_hash = av / 1_000_000
            else: avg_hash = av

            # 2. UPTIME
            uptime = int(summary.get('elapsed') or summary.get('Uptime') or summary.get('Elapsed') or 0)
            
            # 3. ТЕМПЕРАТУРА
            t_list = summary.get('board-temperature') or summary.get('temperature')
            if isinstance(t_list, list):
                temps = [int(float(t)) for t in t_list]
            elif summary.get('Chip Temp Avg'):
                temps.append(int(float(summary['Chip Temp Avg'])))
                
            # 4. ВЕНТИЛЯТОРЫ
            if summary.get('fan-speed-in'): fans.append(str(summary['fan-speed-in']))
            if summary.get('fan-speed-out'): fans.append(str(summary['fan-speed-out']))
            if summary.get('Fan Speed In'): fans.append(str(summary['Fan Speed In']))
            if summary.get('Fan Speed Out'): fans.append(str(summary['Fan Speed Out']))

    # B. Parsing Pools
    if resp_pools:
        # Ищем список пулов в разных местах
        pools_data = resp_pools.get('msg', {}).get('pools')
        if not pools_data: pools_data = resp_pools.get('POOLS')
        
        if pools_data and isinstance(pools_data, list):
            active_pool = None
            for p in pools_data:
                # Статусы бывают: alive, Alive, Active, stratum-active=true
                status = str(p.get('status', '')).lower()
                if status == 'alive' or status == 'active' or p.get('stratum-active') is True:
                    active_pool = p
                    break
            
            if not active_pool: active_pool = pools_data[0]
            
            if active_pool:
                # URL может быть 'url' или 'URL'
                pool_val = active_pool.get('url') or active_pool.get('URL') or ''
                pool = pool_val.replace("stratum+tcp://", "").replace("Stratum+tcp://", "")
                
                # Worker может быть 'user', 'User', 'account'
                worker = active_pool.get('account') or active_pool.get('user') or active_pool.get('User') or ''

    # Fallback данных (если summary было пустым, но info было)
    if not temps and resp_info:
        pwr = resp_info.get('msg', {}).get('power', {})
        if pwr.get('temp0'): temps.append(int(pwr['temp0']))
        
    if not fans and resp_info:
        pwr = resp_info.get('msg', {}).get('power', {})
        if pwr.get('fanspeed'): fans.append(str(pwr['fanspeed']))

    # Финальная обработка
    if avg_hash == 0: avg_hash = raw_hash
    final_real, u_r = normalize_hashrate(raw_hash * 1e12, "T")
    final_avg, u_a = normalize_hashrate(avg_hash * 1e12, "T")

    return {
        "IP": ip, 
        "Make": "MicroBT", 
        "Model": model_full, 
        "Uptime": get_uptime_str(uptime),
        "Real": f"{final_real} {u_r}", 
        "Avg": f"{final_avg} {u_a}", 
        "Fan": " ".join(fans), 
        "Temp": " ".join(str(t) for t in temps), 
        "Pool": pool, 
        "Worker": worker,
        "SortIP": int(ipaddress.IPv4Address(ip)),
        "Algo": algo,
        "RawHash": float(raw_hash)
    }