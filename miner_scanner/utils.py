import socket
import json
import ipaddress
import time
from .config import SOCKET_PORT, TIMEOUT, PING_TIMEOUT, RETRY_COUNT, BUFFER_SIZE

def parse_ip_range(range_str):
    ips = []
    try:
        range_str = range_str.strip()
        if '-' in range_str:
            parts = range_str.split('-')
            start_ip = ipaddress.IPv4Address(parts[0].strip())
            end_part = parts[1].strip()
            if '.' in end_part:
                end_ip = ipaddress.IPv4Address(end_part)
            else:
                base = str(start_ip).rsplit('.', 1)[0]
                end_ip = ipaddress.IPv4Address(f"{base}.{end_part}")
            for ip_int in range(int(start_ip), int(end_ip) + 1):
                ips.append(str(ipaddress.IPv4Address(ip_int)))
        elif '/' in range_str:
            for ip in ipaddress.ip_network(range_str, strict=False).hosts():
                ips.append(str(ip))
        else:
            ips.append(range_str)
    except: pass
    return ips

def get_uptime_str(seconds):
    try: 
        s = int(seconds)
        d, s = divmod(s, 86400)
        h, s = divmod(s, 3600)
        m = s // 60
        return f"{d}d {h}h {m}m"
    except: return "0d 0h 0m"

def normalize_hashrate(val, unit_hint=""):
    try: v = float(val)
    except: return "0.00", "MH/s"
    if v == 0: return "0.00", "MH/s"
    
    # 1. SCRYPT (L7/L9/L3+)
    if unit_hint == "SCRYPT":
        if v >= 1000: 
            return f"{v/1000:.2f}", "GH/s" # Для мощных L7/L9 (9050 MH/s -> 9.05 GH/s)
        return f"{v:.2f}", "MH/s"          # Для старичков L3+ (504 MH/s -> 504.00 MH/s)

    # 2. X11 -> GH/s
    if unit_hint == "X11":
        if v > 100000: return f"{v/1000:.2f}", "GH/s"
        return f"{v:.2f}", "GH/s"

    # 3. ETCHASH -> MH/s
    if unit_hint == "ETCHASH":
        return f"{v:.2f}", "MH/s"
    
    # 4. EQUIHASH -> kSol/s
    if unit_hint == "SOL":
        if v > 1000: return f"{v/1000:.2f}", "kSol/s"
        return f"{v:.2f}", "kSol/s"

    # 5. SHA-256 -> TH/s
    if unit_hint == "T" and v < 5000: return f"{v:.2f}", "TH/s"
    
    if v > 10_000_000_000: return f"{v/1_000_000_000_000:.2f}", "TH/s"
    elif v > 10_000_000: return f"{v/1_000_000:.2f}", "TH/s"
    elif v > 5000: return f"{v/1_000:.2f}", "TH/s"
    else: return f"{v:.2f}", "TH/s"

def check_port(ip, port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(PING_TIMEOUT)
            result = sock.connect_ex((ip, port))
            return result == 0
    except:
        return False