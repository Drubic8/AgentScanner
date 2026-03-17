# miner_scanner/handlers/antminer_dict.py

# Официальные коды ошибок из прошивок Antminer (S19, L9, T21 и др.)
ANTMINER_ERRORS = {
    # === Ошибки Хешрейта и Цепей (R / N) ===
    "R:1": "Average total hashrate is low (Общий хешрейт ниже нормы)",
    "R0:1": "Chain0 is broken, or its hashrate is low (Отвал платы 0 или низкий хешрейт)",
    "R1:1": "Chain1 is broken, or its hashrate is low (Отвал платы 1 или низкий хешрейт)",
    "R2:1": "Chain2 is broken, or its hashrate is low (Отвал платы 2 или низкий хешрейт)",
    "R3:1": "Chain3 is broken, or its hashrate is low (Отвал платы 3 или низкий хешрейт)",
    "N:1": "Average total hashrate exceeds the sale hashrate too much (Хешрейт аномально превышен)",
    "N:2": "Frequency is reduced too much (Частота слишком сильно снижена)",
    "N:4": "Network connection is lost (Потеряно сетевое подключение к пулу)",

    # === Аппаратные ошибки Чипов/Плат (J / L) ===
    "J:8": "The number of hashboards is less than designed (Отвалилась хеш-плата)",
    "J:6": "Temperature sensor error (Сбой датчика температуры)",
    "J0:1": "Chain0 has bad ASIC (Битые чипы на плате 0)",
    "J1:1": "Chain1 has bad ASIC (Битые чипы на плате 1)",
    "J2:1": "Chain2 has bad ASIC (Битые чипы на плате 2)",
    "J3:1": "Chain3 has bad ASIC (Битые чипы на плате 3)",
    "J0:2": "The number of chain0 chips is less than designed (Не хватает чипов на плате 0)",
    "J1:2": "The number of chain1 chips is less than designed (Не хватает чипов на плате 1)",
    "J2:2": "The number of chain2 chips is less than designed (Не хватает чипов на плате 2)",
    "J0:4": "Chain0 EEPROM data error (Ошибка памяти EEPROM на плате 0)",
    "J1:4": "Chain1 EEPROM data error (Ошибка памяти EEPROM на плате 1)",
    "J2:4": "Chain2 EEPROM data error (Ошибка памяти EEPROM на плате 2)",
    "J0:5": "Chain0 PIC error (Ошибка микроконтроллера PIC на плате 0)",
    "J1:5": "Chain1 PIC error (Ошибка микроконтроллера PIC на плате 1)",
    "J2:5": "Chain2 PIC error (Ошибка микроконтроллера PIC на плате 2)",
    "L:2": "Can not find the mixed level (Сбой калибровки напряжения/частот)",
    "L0:1": "Chain0 voltage or frequency exceeds the limit (Сбой напряжения/частоты платы 0)",
    "L1:2": "Chain1 voltage or frequency exceeds the limit (Сбой напряжения/частоты платы 1)",
    "L2:1": "Chain2 voltage or frequency exceeds the limit (Сбой напряжения/частоты платы 2)",

    # === Ошибки Температуры (P) ===
    "P:1": "High temperature protection (Защита от перегрева)",
    "P:2": "Low temperature protection (Защита от переохлаждения)",

    # === Ошибки Кулеров (F) ===
    "F:1": "Fan error (Общая ошибка вентиляторов)",
    "F0:1": "Fan0 is not detected or its speed is low (Отказ кулера 0)",
    "F1:1": "Fan1 is not detected or its speed is low (Отказ кулера 1)",
    "F2:1": "Fan2 is not detected or its speed is low (Отказ кулера 2)",
    "F3:1": "Fan3 is not detected or its speed is low (Отказ кулера 3)",

    # === Ошибки Питания и Памяти (V / M) ===
    "V:1": "Power initialization error or power output voltage error (Сбой инициализации или напряжения БП)",
    "V:2": "Power supply is not calibrated (Блок питания не откалиброван)",
    "M:1": "Memory allocation error (Ошибка выделения памяти OOM)",
}

def get_antminer_error_desc(code):
    if not code:
        return ""
    clean_code = str(code).strip().upper()
    return ANTMINER_ERRORS.get(clean_code, "")