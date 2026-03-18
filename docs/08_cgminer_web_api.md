# CGMiner Web Fallback API Reference

**Модели:** Hammer D10, Bluestar L1 и старые no-name ASIC
**Тип API:** Web Scraping (HTML / CGI)
**Порт:** 80 (HTTP)

## 1. Суть парсера
Многие китайские производители используют "голый" CGMiner без современного JSON API на веб-интерфейсе. 
Программа обращается к веб-страницам и использует **парсинг HTML (Scraping)** с помощью регулярных выражений, так как устройство не умеет отдавать JSON.

## 2. Эндпоинты
* `/cgi-bin/get_system_info.cgi` — Модель, MAC.
* `/cgi-bin/minerStatus.cgi` — HTML-таблица с хешрейтом и аптаймом.
* `/cgi-bin/minerConfiguration.cgi` — Настройки пулов.

## 3. Авторизация
Эндпоинты требуют строгую авторизацию `Basic Auth` или `Digest Auth`. Без логина/пароля (по умолчанию `root:root`) парсинг невозможен.

## 4. Методы извлечения данных (Regex)
* **Uptime:** Извлекается из тега `<cite id="bb_elapsed">(.*?)</cite>`.
* **Хешрейт:** Ищется внутри HTML-таблиц по классам (например, `cbi-table-1-rate` и `cbi-table-1-rate_avg`).
* **Конфигурация:** Внутри HTML-кода ищется вшитая переменная JavaScript `var ant_conf = {...}`, которая затем конвертируется в JSON для извлечения воркеров и пулов.