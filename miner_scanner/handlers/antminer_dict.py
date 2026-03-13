# miner_scanner/handlers/antminer_dict.py

ANTMINER_ERRORS = {
    # Ошибки вентиляторов (Fans)
    "F070": "Вентилятор не обнаружен или скорость 0",
    "F071": "Скорость вентилятора слишком низкая",
    "F072": "Слишком большая разница скоростей вентиляторов",
    
    # Ошибки питания (Power/Voltage)
    "F040": "Сбой чтения напряжения (read_feedback_voltage failed)",
    "F041": "Напряжение слишком низкое (Voltage is too low)",
    "F042": "Напряжение слишком высокое (Voltage is too high)",
    "F110": "Блок питания не отвечает (Power supply error)",
    "F112": "Сбой блока питания (PSU output error)",

    # Ошибки хеш-плат и чипов (Hashboard/Chips)
    "F100": "Не удалось обнаружить все хеш-платы",
    "F101": "Хеш-плата не отвечает",
    "F104": "Сбой инициализации чипов (EEPROM data error)",
    "F106": "Крестики в хеш-плате (ASIC status error)",
    
    # Ошибки температур (Temperatures)
    "F080": "Асик перегрет (High temp protection)",
    "F081": "Сбой датчика температуры на плате",
    
    # Сеть и Пулы (Network/Pools)
    "E001": "Не удалось подключиться к пулу",
    "E002": "Потеряна связь с интернетом",
}

def get_antminer_error_desc(code):
    """Возвращает описание ошибки по её коду"""
    return ANTMINER_ERRORS.get(str(code).upper(), "")