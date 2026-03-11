# miner_scanner/handlers/whatsminer_dict.py

WHATSMINER_ERRORS = {
    # ==========================================
    # 1. ОШИБКИ ВЕНТИЛЯТОРОВ (FANS)
    # ==========================================
    "110": "Fan-in detect speed error (Сбой датчика кулера на вдув)",
    "111": "Fan-out detect speed error (Сбой датчика кулера на выдув)",
    "120": "Fan-in speed error Deviation 2000+ (Отклонение скорости кулера на вдув >2000)",
    "121": "Fan-out speed error Deviation 2000+ (Отклонение скорости кулера на выдув >2000)",
    "130": "Fan-in speed error Deviation 3000+ (Критическое отклонение скорости кулера на вдув)",
    "131": "Fan-out speed error Deviation 3000+ (Критическое отклонение скорости кулера на выдув)",
    "140": "Fan speed is too high (Скорость кулеров слишком высокая - проверьте температуру в помещении)",
    "2310": "Fan 1 speed abnormal (Аномальная скорость кулера 1)",
    "2320": "Fan 2 speed abnormal (Аномальная скорость кулера 2)",
    "2330": "Fan 3 speed abnormal (Аномальная скорость кулера 3)",
    "2340": "Fan 4 speed abnormal (Аномальная скорость кулера 4)",
    "2350": "Fan 5 speed abnormal (Аномальная скорость кулера 5)",

    # ==========================================
    # 2. ОШИБКИ БЛОКА ПИТАНИЯ (POWER SUPPLY)
    # ==========================================
    "200": "Power probing error, no power found (Блок питания не обнаружен или нет связи)",
    "201": "Power supply and config mismatch (Несовпадение модели БП и конфигурации)",
    "202": "Power output voltage error (Ошибка выходного напряжения БП - ожидается сбой)",
    "203": "Power output voltage is too high (Выходное напряжение БП слишком высокое)",
    "204": "Power output voltage is too low (Выходное напряжение БП слишком низкое)",
    "205": "Power input voltage is too high (Входное напряжение сети слишком высокое)",
    "206": "Power input voltage is too low (Входное напряжение сети слишком низкое)",
    "210": "Power temperature is too high (Перегрев блока питания)",
    "213": "Power fan error (Отказ вентилятора в блоке питания)",
    "217": "Power set enable error (Сбой включения/активации БП)",
    "233": "Power output over-current (Перегрузка БП по току - фаза 1)",
    "234": "Power output over-current (Перегрузка БП по току - фаза 2)",
    "235": "Power output over-current (Перегрузка БП по току - фаза 3)",
    "236": "Power output over-current protection (Сработала защита БП от перегрузки по току)",
    "240": "Power output under-voltage protection (Сработала защита БП от низкого напряжения)",
    "249": "Power EEPROM error (Ошибка памяти EEPROM блока питания)",
    "267": "Power supply communication error (Сбой связи с блоком питания)",
    "268": "Power supply communication error (Сбой связи с блоком питания)",
    "269": "Power supply communication error (Сбой связи с блоком питания)",

    # ==========================================
    # 3. ОШИБКИ ТЕМПЕРАТУРЫ (TEMPERATURE)
    # ==========================================
    "300": "Temperature sensor error Hashboard 0 (Ошибка датчика температуры платы 0)",
    "301": "Temperature sensor error Hashboard 1 (Ошибка датчика температуры платы 1)",
    "302": "Temperature sensor error Hashboard 2 (Ошибка датчика температуры платы 2)",
    "320": "Hashboard 0 temperature is too high (Перегрев хеш-платы 0)",
    "321": "Hashboard 1 temperature is too high (Перегрев хеш-платы 1)",
    "322": "Hashboard 2 temperature is too high (Перегрев хеш-платы 2)",
    "350": "Temperature sensor read failed Hashboard 0 (Сбой чтения температуры платы 0)",
    "351": "Temperature sensor read failed Hashboard 1 (Сбой чтения температуры платы 1)",
    "352": "Temperature sensor read failed Hashboard 2 (Сбой чтения температуры платы 2)",
    
    # ==========================================
    # 4. ОШИБКИ ЧИПОВ И ХЕШ-ПЛАТ (HASHBOARDS)
    # ==========================================
    "400": "Chip read error Hashboard 0 (Ошибка чтения чипов на плате 0)",
    "401": "Chip read error Hashboard 1 (Ошибка чтения чипов на плате 1)",
    "402": "Chip read error Hashboard 2 (Ошибка чтения чипов на плате 2)",
    "410": "Chip missing Hashboard 0 (Отсутствуют/не видны чипы на плате 0)",
    "411": "Chip missing Hashboard 1 (Отсутствуют/не видны чипы на плате 1)",
    "412": "Chip missing Hashboard 2 (Отсутствуют/не видны чипы на плате 2)",
    "420": "Chip temperature sensor error HB 0 (Сбой датчика температуры чипа на плате 0)",
    "421": "Chip temperature sensor error HB 1 (Сбой датчика температуры чипа на плате 1)",
    "422": "Chip temperature sensor error HB 2 (Сбой датчика температуры чипа на плате 2)",
    "430": "Chip temperature is too high HB 0 (Перегрев чипов на плате 0)",
    "431": "Chip temperature is too high HB 1 (Перегрев чипов на плате 1)",
    "432": "Chip temperature is too high HB 2 (Перегрев чипов на плате 2)",
    "510": "Hashboard 0 missing (Плата 0 не обнаружена системой)",
    "511": "Hashboard 1 missing (Плата 1 не обнаружена системой)",
    "512": "Hashboard 2 missing (Плата 2 не обнаружена системой)",
    "530": "Hashboard 0 EEPROM error (Ошибка памяти EEPROM платы 0)",
    "531": "Hashboard 1 EEPROM error (Ошибка памяти EEPROM платы 1)",
    "532": "Hashboard 2 EEPROM error (Ошибка памяти EEPROM платы 2)",
    "540": "Hashboard 0 missing or chip error (Плата 0 не обнаружена / Ошибка чипов)",
    "541": "Hashboard 1 missing or chip error (Плата 1 не обнаружена / Ошибка чипов)",
    "542": "Hashboard 2 missing or chip error (Плата 2 не обнаружена / Ошибка чипов)",

    # ==========================================
    # 5. ОШИБКИ СИСТЕМЫ И КОНТРОЛЬНОЙ ПЛАТЫ (CONTROL BOARD)
    # ==========================================
    "600": "Control board temp sensor error (Сбой датчика температуры контрольной платы)",
    "610": "Control board temp is too high (Перегрев контрольной платы)",
    "620": "Control board reboot repeatedly (Циклическая перезагрузка контрольной платы)",

    # ==========================================
    # 6. ОШИБКИ СЕТИ И ПУЛОВ (NETWORK & POOLS)
    # ==========================================
    "701": "Pool 1 connect failed (Ошибка подключения к пулу 1)",
    "702": "Pool 2 connect failed (Ошибка подключения к пулу 2)",
    "703": "Pool 3 connect failed (Ошибка подключения к пулу 3)",
    "711": "Pool 1 authorization failed (Ошибка авторизации/воркера на пулу 1)",
    "712": "Pool 2 authorization failed (Ошибка авторизации/воркера на пулу 2)",
    "713": "Pool 3 authorization failed (Ошибка авторизации/воркера на пулу 3)",
    
    # ==========================================
    # 7. ОШИБКИ ПРОИЗВОДИТЕЛЬНОСТИ (HASHRATE)
    # ==========================================
    "800": "Hashrate is too low (Хешрейт устройства аномально низкий)",
    "801": "Hashboard 0 hashrate is too low (Низкий хешрейт на плате 0)",
    "802": "Hashboard 1 hashrate is too low (Низкий хешрейт на плате 1)",
    "803": "Hashboard 2 hashrate is too low (Низкий хешрейт на плате 2)",

    # ==========================================
    # 8. СПЕЦИФИЧЕСКИЕ (HEX) ОШИБКИ БП
    # ==========================================
    "0x0010": "Primary side over current (Перегрузка на первичной стороне БП)",
    "0x0020": "Output undervoltage (Пониженное выходное напряжение БП)",
    "0x0040": "Output over current - continuous load (Продолжительная перегрузка по току на выходе)",
    "0x0080": "Primary side over current (Перегрузка на первичной стороне БП)",
    "0x0100": "Single circuit overcurrent (Перегрузка одиночной цепи, защита 120A)",
    "0x0200": "Single circuit overcurrent (Перегрузка одиночной цепи, защита 120A)",
    "0x0400": "Single circuit overcurrent (Перегрузка одиночной цепи, защита 120A)",
    "0x0800": "Fan failure in Power Supply (Отказ вентилятора внутри блока питания)",
    "0x1000": "Output over current - short circuit (Короткое замыкание или скачок тока на выходе)",
    "0x2000": "Primary side over voltage (Перенапряжение на первичной стороне БП)",
    "0x4000": "Primary side under voltage (Слишком низкое напряжение на первичной стороне БП)",
    "0x8000": "PFC output over voltage (Перенапряжение на выходе PFC)",

    "550999": "Slot 0 chips reset (Сброс чипов платы 0 - Возможна проблема с БП)",
    "551999": "Slot 1 chips reset (Сброс чипов платы 1 - Возможна проблема с БП)",
    "552999": "Slot 2 chips reset (Сброс чипов платы 2 - Возможна проблема с БП)",
}