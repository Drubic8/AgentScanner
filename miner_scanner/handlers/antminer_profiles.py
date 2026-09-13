def detect_antminer_profile(config):
    """
    Определяет профиль Antminer (Bitmain) на основе ключей в текущем веб-конфиге.
    Возвращает строку: 'Modern', 'Legacy', или 'Unknown'.
    """
    if not isinstance(config, dict):
        return "Unknown"
        
    if "bitmain-work-mode" in config:
        return "Modern" # Модели типа S19, S21+
    elif "miner-mode" in config:
        return "Legacy" # Модели типа D9, D7
    return "Unknown"

def normalize_mode_payload(config, target_mode_int, model=""):
    """
    Подготавливает payload для изменения режима (sleep/normal).
    Использует как текущий конфиг, так и определенную модель (L7, L9, D9, S21).
    """
    payload = dict(config) if isinstance(config, dict) else {}
    m_upper = str(model).upper()

    # Очищаем конфликтующие ключи
    if "bitmain-work-mode" in payload: del payload["bitmain-work-mode"]
    if "miner-mode" in payload: del payload["miner-mode"]

    # 1. СПЕЦ-ЛОГИКА ДЛЯ L9 / L7 / D9 / D7 / Z11
    # Как показывает дамп Wireshark, L9 отдает в get_conf "bitmain-work-mode", 
    # но для успешного сна ТРЕБУЕТ отправки "miner-mode" (int).
    if any(x in m_upper for x in ["L9", "L7", "D9", "D7", "Z11"]):
        payload["miner-mode"] = target_mode_int
        
        # Для этих моделей freq-level обязателен (пустая строка или 100)
        if "freq-level" not in payload or payload["freq-level"] is None:
            payload["freq-level"] = "100" if "L" in m_upper else ""

    # 2. СПЕЦ-ЛОГИКА ДЛЯ PITBIT
    # Дамп показывает, что PitBit принимает минимальный JSON: {"bitmain-work-mode":"0"} (строка)
    elif "PITBIT" in m_upper or "INCM" in m_upper:
        return {"bitmain-work-mode": str(target_mode_int)}

    # 3. СТАНДАРТНЫЕ СОВРЕМЕННЫЕ (S19, S21, T19, T21)
    elif "S21" in m_upper or "S19" in m_upper or "T21" in m_upper or "T19" in m_upper:
        payload["bitmain-work-mode"] = target_mode_int
        if "bitmain-freq-level" not in payload:
            payload["bitmain-freq-level"] = 100

    # 4. ФОЛЛБЭК ДЛЯ НЕИЗВЕСТНЫХ ПРОШИВОК
    else:
        # Отправляем оба ключа, асик сам возьмет нужный
        payload["bitmain-work-mode"] = target_mode_int
        payload["miner-mode"] = target_mode_int
        if "bitmain-freq-level" not in payload: payload["bitmain-freq-level"] = 100
        if "freq-level" not in payload: payload["freq-level"] = ""

    return payload
