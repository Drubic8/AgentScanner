import ipaddress
import re
import socket
from ..utils import get_uptime_str, normalize_hashrate

# ==========================================
# 1. ПАРСИНГ ТЕЛЕМЕТРИИ
# ==========================================
def parse_avalon(ip, resp):
    r_stats = resp.get('stats', {})
    r_summary = resp.get('summary', {})
    r_ver = resp.get('version', {})
    r_pools = resp.get('pools', {})

    stats_data = {}
    if r_stats.get('STATS'):
        stats_data = r_stats['STATS'][0]
        
    summary_data = {}
    if r_summary.get('SUMMARY'):
        summary_data = r_summary['SUMMARY'][0]

    # Извлечение скрытых данных из MM ID0
    for key, value in list(stats_data.items()):
        if key.startswith("MM ID") and isinstance(value, str) and "[" in value:
            matches = re.findall(r'(\w+)\[([^\]]*)\]', value)
            for m_key, m_val in matches:
                stats_data[m_key] = m_val

    # Точное определение модели
    model = "AvalonMiner"
    
    # Способ А: Если сканер смог отправить команду version
    if r_ver.get('VERSION'):
        ver_info = r_ver['VERSION'][0]
        if ver_info.get('MODEL'):
            model = ver_info['MODEL']
        elif ver_info.get('PROD'):
            model = ver_info['PROD']

    # Способ Б (Резервный): Вытаскиваем из параметров stats -> Ver
    if model == "AvalonMiner" and stats_data.get('Ver'):
        ver_str = stats_data['Ver'] 
        parts = ver_str.split('-')
        clean_parts = []
        for p in parts:
            if '_' in p or (len(p) > 10 and p.isdigit()):
                break
            clean_parts.append(p)
        if clean_parts:
            model = "-".join(clean_parts)

    model = str(model).replace("AvalonMiner", "").replace("Avalon", "").strip()
    full_model = f"Avalon {model}"

    uptime = int(summary_data.get('Elapsed', stats_data.get('Elapsed', 0)))

    # Хешрейт (Разделение на Real и Avg)
    ghs_real = 0.0
    if summary_data.get('MHS 1m'):
        ghs_real = float(summary_data['MHS 1m']) / 1000.0
    elif stats_data.get('GHSspd'):
        ghs_real = float(stats_data['GHSspd'])
    elif stats_data.get('GHSmm'):
        ghs_real = float(stats_data['GHSmm'])

    ghs_avg = 0.0
    if summary_data.get('MHS av'):
        ghs_avg = float(summary_data['MHS av']) / 1000.0
    elif stats_data.get('GHSavg'):
        ghs_avg = float(stats_data['GHSavg'])

    real_hash_h = ghs_real * 1e9
    avg_hash_h = ghs_avg * 1e9

    final_real, u_r = normalize_hashrate(real_hash_h, "H")
    final_avg, u_a = normalize_hashrate(avg_hash_h, "H")

    # Температуры (Фокус на лезвия)
    temps = []
    if stats_data.get('MTmax'):
        try:
            parts = str(stats_data['MTmax']).replace('[', '').replace(']', '').split()
            temps = [int(p) for p in parts if p.isdigit()]
        except: pass
        
    if not temps:
        if stats_data.get('TMax'): temps.append(int(stats_data['TMax']))
        if stats_data.get('TAvg'): temps.append(int(stats_data['TAvg']))
        
    temps.sort()
    if len(temps) > 4:
        temps = [temps[0], temps[-1]]

    # Вентиляторы
    fans = []
    for i in range(1, 9):
        f = stats_data.get(f"Fan{i}")
        if f and str(f).isdigit() and int(f) > 0: 
            fans.append(str(f))

    # Пул
    pool, worker = "", ""
    if r_pools.get('POOLS'):
        for p in r_pools['POOLS']:
            if p.get('Status') == 'Alive':
                pool = p.get('URL', '')
                worker = p.get('User', '')
                break
        if not pool and r_pools['POOLS']:
            pool = r_pools['POOLS'][0].get('URL', '')
            worker = r_pools['POOLS'][0].get('User', '')

    pool = pool.replace("Stratum+tcp://", "").replace("stratum+tcp://", "").replace("stratum+ssl://", "")

    return {
        "IP": ip, 
        "Make": "Canaan", 
        "Model": full_model, 
        "Uptime": get_uptime_str(uptime),
        "Real": f"{final_real} {u_r}", 
        "Avg": f"{final_avg} {u_a}",
        "Fan": " ".join(fans), 
        "Temp": " ".join(str(t) for t in temps), 
        "Pool": pool, 
        "Worker": worker,
        "SortIP": int(ipaddress.IPv4Address(ip)),
        "Algo": "SHA-256",
        "RawHash": ghs_avg / 1000.0
    }

# ==========================================
# 2. УПРАВЛЕНИЕ УСТРОЙСТВОМ
# ==========================================
def send_avalon_control(ip: str, command: str) -> bool:
    """Универсальная отправка сырой команды управления на Avalon (порт 4028)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect((ip, 4028))
        s.sendall(command.encode('utf-8'))
        
        response = b""
        while True:
            data = s.recv(4096)
            if not data:
                break
            response += data
            
        s.close()
        resp_str = response.decode('utf-8', errors='ignore')
        
        if "STATUS=S" in resp_str or "STATUS=I" in resp_str:
            return True
        return False
    except Exception:
        return False

def avalon_reboot(ip: str) -> bool:
    return send_avalon_control(ip, "ascset|0,reboot,1")

def avalon_led_toggle(ip: str) -> bool:
    return send_avalon_control(ip, "ascset|0,led,0-1")

def avalon_set_sleep(ip: str) -> bool:
    return send_avalon_control(ip, "ascset|0,softoff")

def avalon_set_normal(ip: str) -> bool:
    # Пробуждение осуществляется через команду перезагрузки
    return send_avalon_control(ip, "ascset|0,reboot,1")