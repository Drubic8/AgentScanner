import logging
import ipaddress
from ..utils import get_uptime_str
from .antminer_stock import get_6060_errors  # <-- Импортируем ваш метод опроса 6060 порта

logger = logging.getLogger(__name__)

def parse_antminer_pitbit(ip, resp):
    """
    Парсер для кастомной прошивки PitBit (Antminer S19/S21).
    Полностью интегрирован с вашей системой детекта нестабильных плат (chain_acs) и ошибок порта 6060.
    """
    try:
        version_data = resp.get('version', {}).get('VERSION', [{}])[0]
        stats_data = resp.get('stats', {}).get('STATS', [{}, {}])
        pools_data = resp.get('pools', {}).get('POOLS', [])

        # У PitBit название модели лежит в 0-м индексе, а сама стата в 1-м
        meta_data = stats_data[0] if stats_data else {}
        s_data = stats_data[1] if len(stats_data) > 1 else meta_data

        # --- 1. Модель, Аптайм и Сортировка IP ---
        model = meta_data.get('Type') or version_data.get('Type') or s_data.get('Type', 'Unknown PitBit')
        uptime_sec = int(s_data.get('Elapsed', 0))
        sort_ip = int(ipaddress.IPv4Address(ip))

        # --- 2. Хешрейт (Моментальный и Средний) ---
        ghs_5s = float(s_data.get('GHS 5s') or s_data.get('rate_30m') or 0)
        ghs_av = float(s_data.get('GHS av') or ghs_5s or 0)
        
        real_hr = f"{round(ghs_5s / 1000.0, 2)} TH/s" if ghs_5s > 1000 else f"{round(ghs_5s, 2)} GH/s"
        avg_hr = f"{round(ghs_av / 1000.0, 2)} TH/s" if ghs_av > 1000 else f"{round(ghs_av, 2)} GH/s"

        # --- 3. Вентиляторы и поиск отвалов кулеров ---
        fans = []
        broken_fans = []
        for i in range(1, 5):
            if f"fan{i}" in s_data:
                f_val = int(s_data.get(f"fan{i}", 0))
                if f_val > 0:
                    fans.append(str(f_val))
                else:
                    broken_fans.append(str(i))
                    
        fan_str = " ".join(fans)

        # --- 4. Температуры ---
        temps = []
        for i in range(1, 5):
            chip_str = s_data.get(f'temp_chip{i}')
            pcb_str = s_data.get(f'temp_pcb{i}') or s_data.get(f'temp{i}')
            
            c_max, p_max = 0, 0
            if chip_str and isinstance(chip_str, str):
                c_vals = [int(x) for x in chip_str.split('-') if x.isdigit()]
                if c_vals: c_max = max(c_vals)
            elif isinstance(chip_str, (int, float)):
                c_max = int(chip_str)
                
            if pcb_str and isinstance(pcb_str, str):
                p_vals = [int(x) for x in pcb_str.split('-') if x.isdigit()]
                if p_vals: p_max = max(p_vals)
            elif isinstance(pcb_str, (int, float)):
                p_max = int(pcb_str)
                
            if c_max > 0 or p_max > 0:
                temps.append(f"{c_max}/{p_max}")
                
        temp_str = " ".join(temps)

        # --- 5. Пулы ---
        pool, worker = "", ""
        for p in pools_data:
            if str(p.get('Status', '')).lower() in ['alive', 'stratum active']:
                pool = p.get('URL', '')
                worker = p.get('User', '')
                break
        if not pool and pools_data:
            pool = pools_data[0].get('URL', '')
            worker = pools_data[0].get('User', '')

        # --- 6. Алгоритм ---
        algo = "SHA-2-56" if hasattr(resp, '_fake_trigger') else "SHA-256" # Фикс дефиса, теперь SHA-256

        # --- 7. ВАША ЛОГИКА ОПРЕДЕЛЕНИЯ НЕСТАБИЛЬНОСТИ ПЛАТ И ОШИБОК ---
        errors_list = []
        details_list = []
        status = "Running"

        # Проверка по вашей методике: ищем крестики 'x' или '-' в цепях чипов
        failed_boards = []
        has_hw_error = False
        for i in range(1, 5):
            chain_key = f"chain_acs{i}"
            if chain_key in s_data:
                val_str = str(s_data[chain_key]).lower()
                if 'x' in val_str or '-' in val_str:
                    has_hw_error = True
                    failed_boards.append(str(i))
        
        if has_hw_error:
            status = "Error"
            boards_str = ",".join(failed_boards)
            errors_list.append(f"HW ERR (B{boards_str})")
            details_list.append(f"Нестабильные чипы ('x', '-') на плате {boards_str}")

        # Доп. проверка для PitBit: если плата есть, но её хэшрейт упал в 0, пока остальные работают
        for i in range(1, 4):
            rate_key = f"chain_rate{i}"
            acn_key = f"chain_acn{i}"
            if s_data.get(acn_key, 0) > 0 and s_data.get(rate_key) in ["0", "0.00", 0, 0.0] and ghs_5s > 0:
                status = "Error"
                if f"B{i} DOWN" not in errors_list:
                    errors_list.append(f"B{i} DOWN")
                    details_list.append(f"Плата {i} перестала хэшировать (0 GH/s)")

        # Проверка кулеров
        if broken_fans:
            status = "Error"
            errors_list.append("FAN ERR")
            details_list.append(f"Отвал кулера №{', '.join(broken_fans)} (0 RPM)")

        # Опрос вашего лога ошибок с 6060 порта
        short_6060_err, detail_6060_err = get_6060_errors(ip)
        if short_6060_err:
            status = "Error"
            errors_list.append(short_6060_err)
            details_list.append(detail_6060_err)

        # Проверка режима сна или полного отсутствия хэша
        mode = s_data.get('Mode', 0)
        if ghs_5s == 0:
            if uptime_sec < 240:
                status = "Init"
            elif int(mode) == 1:
                status = "Sleep"
                errors_list = []  # В спячке кулеры и хэш на нуле легально
                details_list = []
            else:
                status = "Error"
                if not errors_list:
                    errors_list.append("NO HASH")
                    details_list.append("Хешрейт 0 TH/s (аппарат завис)")

        # Собираем строки под интерфейс
        error_brief = " + ".join(errors_list) if errors_list else ""
        error_details = "\n".join(details_list) if details_list else ""

        return {
            "IP": ip,
            "SortIP": sort_ip,
            "Make": "Bitmain",
            "Model": model,
            "Algo": "SHA-256",
            "Uptime": get_uptime_str(uptime_sec),
            "Real": real_hr,
            "Avg": avg_hr,
            "Fan": fan_str,
            "Temp": temp_str,
            "Pool": pool,
            "Worker": worker,
            "Status": status,
            "Error": error_brief,
            "ErrorDetails": error_details
        }

    except Exception as e:
        logger.error(f"[{ip}] Ошибка парсинга PitBit: {e}")
        return None