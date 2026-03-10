import json
import ipaddress
import socket
import struct
from ..utils import get_uptime_str, normalize_hashrate

# === ПРОСТОЙ TCP КЛИЕНТ (Без шифрования) ===
class SimpleWhatsminerTCP:
    def __init__(self, ip, port, timeout=3):
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
            
            chunks = []
            bytes_recd = 0
            while bytes_recd < pkg_len:
                chunk = self.sock.recv(min(pkg_len - bytes_recd, 4096))
                if not chunk: break
                chunks.append(chunk)
                bytes_recd += len(chunk)
                
            raw_resp = b''.join(chunks).decode('utf-8')
            return json.loads(raw_resp)
        except:
            return None

# === ПАРСЕР ===
def parse_whatsminer_v3(ip, port=8889):
    tcp = SimpleWhatsminerTCP(ip, port)
    if not tcp.connect():
        return None

    model_full = "Whatsminer V3"
    algo = "SHA-256"
    
    # 1. ЗАПРОС ИНФО (для модели)
    resp_info = tcp.send_cmd("get.device.info")
    if resp_info and resp_info.get('code') == 0:
        info = resp_info.get('msg', {})
        m_type = info.get('miner', {}).get('type', 'Unknown')
        model_full = f"Whatsminer {m_type}"
        ctype = info.get('miner', {}).get('cointype', 'SHA-256')
        if 'BTC' in str(ctype).upper(): algo = "SHA-256"
        else: algo = ctype

    # 2. ЗАПРОС СТАТИСТИКИ (Хешрейт, Темп)
    resp_summary = tcp.send_cmd("get.miner.status", "summary")
    
    # 3. ЗАПРОС ПУЛОВ (Pool, Worker)
    resp_pools = tcp.send_cmd("get.miner.status", "pools")
    
    tcp.close()

    # --- ПАРСИНГ ---
    raw_hash = 0.0
    avg_hash = 0.0
    uptime = 0
    temps = []
    fans = []
    pool = ""
    worker = ""
    
    # A. Parsing Summary
    if resp_summary:
        code = resp_summary.get('code')
        if code == -1: model_full += " (Miner Down)"
        
        summary = resp_summary.get('msg', {}).get('summary', {})
        if summary:
            raw_hash = float(summary.get('hash-realtime', 0))
            avg_hash = float(summary.get('hash-average', 0))
            uptime = int(summary.get('elapsed', 0))
            
            t_list = summary.get('board-temperature', [])
            if isinstance(t_list, list):
                temps = [int(float(t)) for t in t_list]
                
            if summary.get('fan-speed-in'): fans.append(str(summary['fan-speed-in']))
            if summary.get('fan-speed-out'): fans.append(str(summary['fan-speed-out']))

    # B. Parsing Pools (ИСПРАВЛЕНО: ключи url и account)
    if resp_pools and resp_pools.get('code') == 0:
        pools_data = resp_pools.get('msg', {}).get('pools', [])
        if pools_data and isinstance(pools_data, list):
            # Ищем активный пул (где status='alive' или 'stratum-active'=true)
            active_pool = None
            for p in pools_data:
                # Обычно первый пул активный, но можно проверить статус
                if p.get('status') == 'alive' or p.get('stratum-active') is True:
                    active_pool = p
                    break
            
            # Если не нашли активный по статусу, берем первый
            if not active_pool and pools_data:
                active_pool = pools_data[0]
            
            if active_pool:
                # ВОТ ТУТ БЫЛА ОШИБКА: V3 использует 'url' и 'account'
                pool_val = active_pool.get('url', '')
                pool = pool_val.replace("stratum+tcp://", "").replace("Stratum+tcp://", "")
                worker = active_pool.get('account', '')

    # Fallback данных
    if not temps and resp_info:
        pwr = resp_info.get('msg', {}).get('power', {})
        if pwr.get('temp0'): temps.append(int(pwr['temp0']))
        
    if not fans and resp_info:
        pwr = resp_info.get('msg', {}).get('power', {})
        if pwr.get('fanspeed'): fans.append(str(pwr['fanspeed']))

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