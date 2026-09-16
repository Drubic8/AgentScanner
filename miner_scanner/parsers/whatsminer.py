import ipaddress
import re
from ..utils import get_uptime_str, normalize_hashrate

from ..handlers.whatsminer_dict import WHATSMINER_ERRORS

def safe_float(val, mult=1.0):
    try:
        if val is not None and str(val).strip() != "":
            return float(val) * mult
    except: pass
    return 0.0

def parse_whatsminer_data(ip, resp_info, resp_summary, resp_pools):
    error_details_str = ""
    model_full = "Whatsminer Detected"
    algo = "SHA-256"
    error_code = ""
    
    # 1. ЗАПРОС ИНФО (Модель и Ошибки)
    working_status = "unknown" 
    
    if resp_info and resp_info.get('code') == 0:
        info = resp_info.get('msg', {})
        
        if isinstance(info, dict):
            m_type = info.get('miner', {}).get('type') or \
                     info.get('sub_version') or \
                     info.get('product_type')
            if m_type: model_full = f"Whatsminer {m_type}"
            
            # --- СЧИТЫВАЕМ СТАТУС WORKING ---
            working_status = str(info.get('miner', {}).get('working', 'unknown')).lower()
            
            ctype = info.get('miner', {}).get('cointype', 'SHA-256')
            if 'BTC' not in str(ctype).upper(): algo = str(ctype)

            # === УМНОЕ ИЗВЛЕЧЕНИЕ КОДОВ ОШИБОК И ИХ ОПИСАНИЙ ===
            err = info.get('error-code') or info.get('error_code')
            err_list = []
            err_details = [] 

            def get_error_description(code_str, api_reason):
                if code_str in WHATSMINER_ERRORS:
                    return WHATSMINER_ERRORS[code_str]
                
                if len(code_str) == 6 and code_str.startswith("5") and code_str[1] in "456":
                    err_type = code_str[:2]
                    board = code_str[2]    
                    chip = code_str[3:]    
                    chip_text = "всех чипов" if chip == "999" else f"чипа №{int(chip)}"
                    
                    if err_type == "54":
                        return f"Slot {board} chip error (Сбой {chip_text} на плате {board} - Требуется ремонт платы)"
                    elif err_type == "55":
                        return f"Slot {board} chips reset (Сброс {chip_text} на плате {board} - Возможна проблема с БП)"
                    elif err_type == "56":
                        return f"Slot {board} chip error (Ошибка {chip_text} на плате {board})"

                return api_reason if api_reason else "Неизвестная ошибка"

            if err:
                if isinstance(err, list):
                    for e_item in err:
                        if isinstance(e_item, dict):
                            reason_api = e_item.get('reason', '').strip()
                            for k in e_item.keys():
                                if k != 'reason' and str(k) != "0":
                                    k_str = str(k)
                                    err_list.append(k_str)
                                    desc = get_error_description(k_str, reason_api)
                                    err_details.append(f"Код {k_str}: {desc}")
                        elif str(e_item) != "0":
                            k_str = str(e_item)
                            err_list.append(k_str)
                            desc = get_error_description(k_str, "")
                            err_details.append(f"Код {k_str}: {desc}")
                elif isinstance(err, dict):
                    reason_api = err.get('reason', '').strip()
                    for k in err.keys():
                        if k != 'reason' and str(k) != "0":
                            k_str = str(k)
                            err_list.append(k_str)
                            desc = get_error_description(k_str, reason_api)
                            err_details.append(f"Код {k_str}: {desc}")
                elif str(err) != "0":
                    k_str = str(err)
                    err_list.append(k_str)
                    desc = get_error_description(k_str, "")
                    err_details.append(f"Код {k_str}: {desc}")
                    
            if err_list:
                error_code = "-".join(err_list)
                error_details_str = "\n".join(err_details)
            else:
                error_details_str = ""

    raw_hash = 0.0
    avg_hash = 0.0
    uptime = 0
    temps = []
    fans = []
    pool = ""
    worker = ""
    
    if resp_summary:
        msg = resp_summary.get('msg', {})
        if isinstance(msg, dict):
            if not msg and 'Msg' in resp_summary: msg = resp_summary['Msg']
            
            summary = msg.get('summary') if isinstance(msg, dict) else msg 
            if not summary: summary = msg

            if summary and isinstance(summary, dict):
                rt = safe_float(summary.get('hash-realtime')) or safe_float(summary.get('HS RT')) or safe_float(summary.get('GHS 5s'), 1000)
                av = safe_float(summary.get('hash-average')) or safe_float(summary.get('MHS av')) or safe_float(summary.get('GHS av'), 1000)
                
                if rt > 10000: raw_hash = rt / 1_000_000
                else: raw_hash = rt

                if av > 10000: avg_hash = av / 1_000_000
                else: avg_hash = av

                uptime = int(safe_float(summary.get('elapsed') or summary.get('Uptime') or summary.get('Elapsed')))
                
                t_list = summary.get('board-temperature') or summary.get('temperature')
                if isinstance(t_list, list):
                    temps = [int(safe_float(t)) for t in t_list]
                elif summary.get('Chip Temp Avg'):
                    temps.append(int(safe_float(summary['Chip Temp Avg'])))
                    
                for fan_key in ['fan-speed-in', 'fan-speed-out', 'Fan Speed In', 'Fan Speed Out']:
                    if summary.get(fan_key): fans.append(str(summary[fan_key]))

    if resp_pools:
        msg = resp_pools.get('msg', {})
        if isinstance(msg, dict):
            pools_data = msg.get('pools') or resp_pools.get('POOLS')
            if pools_data and isinstance(pools_data, list):
                active_pool = next((p for p in pools_data if str(p.get('status', '')).lower() in ['alive', 'active'] or p.get('stratum-active') is True), pools_data[0])
                pool = (active_pool.get('url') or active_pool.get('URL') or '').replace("stratum+tcp://", "").replace("Stratum+tcp://", "")
                worker = active_pool.get('account') or active_pool.get('user') or active_pool.get('User') or ''

    # Если температуры и кулеры не пришли в summary, берем из device.info
    if resp_info and isinstance(resp_info.get('msg'), dict):
        pwr = resp_info.get('msg', {}).get('power', {})
        if not temps and pwr.get('temp0'): temps.append(int(safe_float(pwr['temp0'])))
        if not fans and pwr.get('fanspeed'): fans.append(str(pwr['fanspeed']))

    if avg_hash == 0: avg_hash = raw_hash
    final_real, u_r = normalize_hashrate(raw_hash * 1e12, "T")
    final_avg, u_a = normalize_hashrate(avg_hash * 1e12, "T")

    # === ОПРЕДЕЛЯЕМ ФИНАЛЬНЫЙ СТАТУС УСТРОЙСТВА ===
    summary_msg_str = ""
    if resp_summary:
        msg_val = resp_summary.get('msg', '')
        if isinstance(msg_val, str):
            summary_msg_str = msg_val.lower()

    if "down" in summary_msg_str or "stop" in summary_msg_str:
        final_status = "Sleep"         # Старый API (процесс убит)
    elif working_status == "true":
        final_status = "Running"       # Майнит (Штатная работа)
    elif error_code:
        final_status = "Error"         # Не майнит, есть аппаратные ошибки
    elif raw_hash == 0 and working_status == "false":
        # Если хеша нет, ошибок нет, и он не "working"
        # Обычно при старте асик быстро переходит в прогрев,
        # если он просто висит с нулевым хешем - это принудительный Sleep.
        if uptime > 60:
            final_status = "Sleep"     # Спит больше минуты
        else:
            final_status = "WaitWork"  # Только-только запустился
    else:
        final_status = "WaitWork"

    return {
        "IP": ip, 
        "Make": "MicroBT", 
        "Model": model_full, 
        "Algo": algo,
        "Status": final_status,
        "Uptime": get_uptime_str(uptime),
        "Real": f"{final_real} {u_r}", 
        "Avg": f"{final_avg} {u_a}", 
        "Fan": " ".join(fans), 
        "Temp": " ".join(str(t) for t in temps), 
        "Pool": pool, 
        "Worker": worker,
        "SortIP": int(ipaddress.IPv4Address(ip)),
        "RawHash": float(raw_hash),
        "Error": error_code,
        "ErrorDetails": error_details_str
    }
