# VNish Firmware API Reference

**Официальная документация:** Встроенный Swagger UI (xminer-api v0.1.0)
**Оригинальный дамп:** Сохранен в `vnish_openapi.json`
**Тип API:** REST API (JSON)
**Базовый URL:** `http://{ip}/api/v1`

## 1. Авторизация (Security)
VNish использует продвинутую систему авторизации (Bearer Token / API Key), в отличие от старых Basic/Digest методов:
1. Запросить токен: `POST /api/v1/unlock`
   * **Body:** `{"pw": "your_password"}`
   * **Response:** Возвращает объект с полем `token`.
2. Использовать токен в заголовке `Authorization: Bearer <token>` для всех закрытых эндпоинтов (управление).
*Чтение статистики (GET-запросы) обычно доступно без авторизации.*

## 2. Мониторинг и Статистика (GET)
* **Инфо (`/info`):** Возвращает версию прошивки (`fw_version`), алгоритм, платформу и базовую систему.
* **Сводка (`/summary`):** Главный эндпоинт для сканера. Возвращает объект `AntmMinerStats`, который включает:
  * Статус устройства (`miner_status.miner_state`): `mining`, `initializing`, `stopped`, `failure` и др.
  * Хешрейт: `hr_realtime`, `hr_average`, `hr_stock`.
  * Платы (`chains`), Кулеры (`cooling`), Пулы (`pools`), Температуры (`chip_temp`, `pcb_temp`).

## 3. Управление майнером (POST)
Для этих команд требуется авторизация (токен):

* **Поиск асика (Лампочка):** `/find-miner`
  * Payload: `{"on": true}` или `{"on": false}`
* **Управление майнингом (Сон / Пробуждение):** * Уснуть: `/mining/stop` (или `/mining/pause`)
  * Проснуться: `/mining/start` (или `/mining/resume`)
* **Смена пулов:** `/mining/switch-pool`
  * Payload: `{"pool_id": 1}`
* **Перезагрузка:** `/system/reboot`

## 4. Определение статуса (MinerState)
Прошивка сама отдает точный статус в поле `miner_state`. Варианты:
* `mining` — Штатная работа.
* `initializing`, `starting`, `auto-tuning` — Прогрев / Настройка (WaitWork).
* `stopped` — Принудительно остановлен (Sleep).
* `failure` — Аппаратная ошибка (Error).
* `shutting-down`, `restarting` — Перезагрузка.