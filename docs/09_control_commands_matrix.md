# Control Commands Matrix

> Справочник исследованных API, не подтверждение поддержки конкретной прошивки.
> Критерии приёмки и реальные результаты ведутся в [матрице испытаний](testing/README.md).
> Ответ HTTP 200 или таймаут перезагрузки сами по себе не подтверждают успех.
> Авторизацию и формат команды определяет профиль; автоматическое угадывание не допускается.

Сводная таблица по управлению различными семействами оборудования.

## Единые команды сна и пробуждения

В GUI доступны **Сон (остановить майнинг)** и **Пробуждение (возобновить майнинг)**.
Можно выбрать устройства разных моделей и прошивок из нескольких подсетей и
отправить одно действие всем выбранным устройствам. Ядро преобразует `sleep` в
`mining_stop`, а `wakeup` в `mining_start`; старое имя `normal` сохраняется как
совместимый псевдоним пробуждения.

Далее для каждого IP отдельно проверяются актуальная идентификация, доступ и
совместимость API. Поддерживаемые устройства получают команду своего интерфейса;
отказ одного устройства не останавливает обработку остальных. Журнал содержит
результат для каждого IP. Неподдерживаемые команды не отправляются.

Для стоковых Antminer с подтверждённым контрактом пробуждение выбирает режим 0
(Normal). Для VNish запускается майнинг, для WhatsMiner — служба майнинга, для
Elphapex — режим 0. У проверенного Avalon 1346-110 пробуждение выполняется через
перезагрузку; GUI предупреждает об этом перед выполнением.

**Режим мощности** — отдельное подменю: Low, Normal и HEM доступны по
возможностям выбранных устройств. Пробуждение не гарантирует восстановления
ранее выбранного Low/HEM; поведение зависит от интерфейса прошивки. Подтверждение
настройки или запуска не означает, что устройство уже достигло рабочего хешрейта.

## Интерфейсы устройств

| Vendor | Model Family | Command | Endpoint | Auth | Payload / Fields | Success Criteria |
|--------|--------------|---------|----------|------|------------------|------------------|
| **Bitmain** | Antminer Modern (S19, S21) | Sleep / Normal | `POST /cgi-bin/set_miner_conf.cgi` | Digest/Basic `root:root` | `bitmain-work-mode: 0/1` (int), `bitmain-freq-level: 100` (int) | HTTP 200 + Re-read config matches |
| **Bitmain** | Antminer Legacy (D9, D7) | Sleep / Normal | `POST /cgi-bin/set_miner_conf.cgi` | Digest/Basic `root:root` | `miner-mode: 0/1` (int), `freq-level: ""` (str) | HTTP 200 + Re-read config matches |
| **Bitmain** | Antminer (Stock) | LED On / Off | `POST /cgi-bin/blink.cgi` | Digest/Basic `root:root` | `{"blink": true/false}` (json) | HTTP 200 |
| **Bitmain** | Antminer (Stock) | Reboot | `GET /cgi-bin/reboot.cgi` | Digest/Basic `root:root` | None | HTTP 200 or Timeout (Device restarted) |
| **VNish** | Antminer (Custom) | Sleep / Normal | `POST /api/v1/mining/stop` or `start` | Token / Digest `root` | None | HTTP 200 or 204 |
| **VNish** | Antminer (Custom) | Reboot | `POST /api/v1/system/reboot` | Token / Digest `root` | None | HTTP 200 or 204 |
| **MicroBT** | Whatsminer (API v3) | Sleep / Normal | `TCP 4433` | Raw Hash `super:pwd` | `set.miner.service: stop/start` | Response `code: 0` |
| **MicroBT** | Whatsminer (API v3) | Reboot | `TCP 4433` | Raw Hash `super:pwd` | `set.system.reboot` | Response `code: 0` |
| **Canaan** | Avalon 1346-110 / MM317 | Sleep / Wakeup (Reboot) | `TCP 4028` | Нет для проверенного устройства | Text: `ascset\|0,softoff` / `ascset\|0,reboot,1`; захват FMS 3.3.4 | Sleep: переход в Idle с отключением плат; Wakeup: сброс uptime, разгон отдельно |
| **Canaan** | Avalon с подтверждённым LED API | LED On / Off | `TCP 4028` | Нет для проверенного устройства | Text: `ascset\|0,led,1-1` / `ascset\|0,led,1-0` | Повторное чтение `ascset\|0,led,1-255` → `LED[1]` / `LED[0]` |
| **Elphapex** | DG-Series | Sleep / Normal | `POST /cgi-bin/luci/setworkmode.cgi`| None | `workmode: "-1000" / "0"` | HTTP 200 |
| **Jasminer** | X-Series | LED On / Off | `POST /cgi-bin/find_miner_on.cgi` | Digest `root:root` | None | HTTP 200 and "ok" in text |
