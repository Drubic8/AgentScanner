import ipaddress
import re
from ..utils import get_uptime_str, normalize_hashrate

def parse_avalon(ip, resp):
    r_stats = resp.get('stats', {})
    r_ver = resp.get('version', {})
    r_pools = resp.get('pools', {})

    # ==========================================
    # 1. ПАРСИНГ ДАННЫХ (STATS)
    # ==========================================
    stats_data = {}
    if r_stats.get('STATS'):
        stats_data = r_stats['STATS'][0]
    
    # Пытаемся достать скрытые данные из MM ID (там часто лежит реальная версия и конфиг)
    for key, value in list(stats_data.items()):
        if key.startswith("MM ID") and isinstance(value, str) and "[" in value:
            matches = re.findall(r'(\w+)\[([^\]]*)\]', value)
            for m_key, m_val in matches:
                stats_data[m_key] = m_val

    # ==========================================
    # 2. ОПРЕДЕЛЕНИЕ МОДЕЛИ (УЛУЧШЕНО)
    # ==========================================
    model = "AvalonMiner"
    
    # Способ А: Из команды 'version'
    if r_ver.get('VERSION'):
        m = r_ver['VERSION'][0].get('PROD')
        if m: model = m
    
    # Способ Б: Из 'MM ID' -> 'Ver' (если способ А дал дефолтное имя)
    if model == "AvalonMiner" and stats_data.get('Ver'):
        ver_str = stats_data['Ver'] 
        # Пример 1: "1126Pro-S-68-21072803_4ec6bb0_211fc46"
        # Пример 2: "1346-110-24041001_08b0955_0196aba"
        
        parts = ver_str.split('-')
        clean_parts = []
        
        for p in parts:
            # Если часть содержит "_", это явно версия прошивки/хэш -> останавливаемся
            if '_' in p:
                break
            # Если часть слишком длинная и цифровая (похожа на дату 20240101), тоже стоп
            if len(p) > 10 and p.isdigit():
                break
            clean_parts.append(p)
            
        if clean_parts:
            model = "-".join(clean_parts)
        else:
            model = ver_str

    # Финальная очистка
    model = str(model).replace("AvalonMiner", "").replace("Avalon", "").strip()
    full_model = f"Avalon {model}"

    # ==========================================
    # 3. UPTIME
    # ==========================================
    uptime = int(stats_data.get('Elapsed', 0))
    
    # ==========================================
    # 4. ХЕШРЕЙТ (GHS -> THS)
    # ==========================================
    ghs_val = stats_data.get('GHS 5s')
    if not ghs_val or float(ghs_val) == 0:
        ghs_val = stats_data.get('GHSavg', 0)
    if not ghs_val or float(ghs_val) == 0:
        ghs_val = stats_data.get('GHSmm', 0)
        
    try:
        raw_ghs = float(ghs_val)
    except:
        raw_ghs = 0.0

    raw_th = raw_ghs / 1000.0
    raw_hash_h = raw_ghs * 1e9 

    # ==========================================
    # 5. ТЕМПЕРАТУРЫ
    # ==========================================
    temps = []
    # Вариант 1: Явные ключи
    if stats_data.get('TMax'): temps.append(int(stats_data['TMax']))
    if stats_data.get('TAvg'): temps.append(int(stats_data['TAvg']))
    
    # Вариант 2: Строка MTmax (Пример: "98 81 91")
    if not temps and stats_data.get('MTmax'):
        try:
            parts = str(stats_data['MTmax']).replace('[', '').replace(']', '').split()
            for p in parts: 
                if p.isdigit(): temps.append(int(p))
        except: pass
        
    temps.sort()
    if len(temps) > 4:
        temps = [temps[0], temps[-1]]

    # ==========================================
    # 6. ВЕНТИЛЯТОРЫ
    # ==========================================
    fans = []
    for i in range(1, 9):
        f = stats_data.get(f"Fan{i}")
        if f and str(f).isdigit() and int(f) > 0: 
            fans.append(str(f))

    # ==========================================
    # 7. ПУЛ
    # ==========================================
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

    final_real, u_r = normalize_hashrate(raw_hash_h, "H")
    
    return {
        "IP": ip, 
        "Make": "Canaan", 
        "Model": full_model, 
        "Uptime": get_uptime_str(uptime),
        "Real": f"{final_real} {u_r}", 
        "Avg": f"{final_real} {u_r}",
        "Fan": " ".join(fans), 
        "Temp": " ".join(str(t) for t in temps), 
        "Pool": pool, 
        "Worker": worker,
        "SortIP": int(ipaddress.IPv4Address(ip)),
        "Algo": "SHA-256",
        "RawHash": raw_th
    }