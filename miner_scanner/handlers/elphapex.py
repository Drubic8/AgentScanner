import requests
import ipaddress
from requests.auth import HTTPDigestAuth
# ВАЖНО: Импорт из utils, а не formatters
from ..utils import get_uptime_str, normalize_hashrate

def scan_elphapex(ip):
    try:
        url = f"http://{ip}/cgi-bin/stats.cgi"
        # Короткий таймаут, чтобы не висело
        r = requests.get(url, timeout=2)
        if r.status_code == 401:
            r = requests.get(url, auth=HTTPDigestAuth('root', 'root'), timeout=2)
        
        if r.status_code != 200:
            return None

        d = r.json()
        # Защита от пустого ответа
        s = d.get('STATS', [{}])[0]
        
        # Формируем модель
        info = d.get('INFO', {})
        model_type = info.get('type', 'DG1')
        model = f"Elphapex {model_type}"
        
        # Парсим хешрейт
        real_s, u_r = normalize_hashrate(s.get('rate_15m', 0), "SCRYPT")
        avg_s, u_a = normalize_hashrate(s.get('rate_avg', 0), "SCRYPT")
        
        # Вентиляторы
        fans = [str(f) for f in s.get('fan', [])]
        
        # Температуры
        temps = []
        if 'chain' in s:
            for c in s['chain']:
                val_final = 0
                
                # Функция очистки значений
                def clean_temp(val):
                    try:
                        v = float(val)
                        if v > 200: return v / 1000
                        return v
                    except: return 0

                # 1. Приоритет: Чипы
                t_chip_raw = c.get('temp_chip')
                if isinstance(t_chip_raw, list):
                    chips = [clean_temp(x) for x in t_chip_raw]
                    if chips: val_final = max(chips)
                elif isinstance(t_chip_raw, (int, float, str)):
                    val_final = clean_temp(t_chip_raw)

                # 2. Фолбэк: Плата (если чипы 0)
                if val_final == 0:
                    t_pcb_raw = c.get('temp_pcb')
                    if isinstance(t_pcb_raw, list):
                        pcbs = [clean_temp(x) for x in t_pcb_raw]
                        if pcbs: val_final = max(pcbs)
                    elif isinstance(t_pcb_raw, (int, float)):
                        val_final = clean_temp(t_pcb_raw)
                    elif isinstance(t_pcb_raw, str) and '/' in t_pcb_raw:
                        try: val_final = clean_temp(t_pcb_raw.split('/')[1])
                        except: pass

                if val_final > 0:
                    temps.append(int(val_final))

        # Пул и воркер
        pool, work = "", ""
        try:
            conf_url = f"http://{ip}/cgi-bin/get_miner_conf.cgi"
            pc = requests.get(conf_url, auth=HTTPDigestAuth('root', 'root'), timeout=2).json()
            if 'pools' in pc and len(pc['pools']) > 0:
                pool = pc['pools'][0]['url'].replace("stratum+tcp://", "")
                work = pc['pools'][0]['user']
        except: pass

        return {
            "IP": ip, "Make": "Elphapex", "Model": model,
            "Uptime": get_uptime_str(s.get('elapsed', 0)),
            "Real": f"{real_s} {u_r}", "Avg": f"{avg_s} {u_a}",
            "Fan": " ".join(fans), "Temp": " ".join(str(t) for t in temps),
            "Pool": pool, "Worker": work,
            "SortIP": int(ipaddress.IPv4Address(ip)), "Algo": "Scrypt",
            "RawHash": float(str(real_s).replace(',', ''))
        }
    except Exception:
        return None