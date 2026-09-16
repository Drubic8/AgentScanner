# Control Commands Matrix

> Справочник исследованных API, не подтверждение поддержки конкретной прошивки.
> Критерии приёмки и реальные результаты ведутся в [матрице испытаний](testing/README.md).
> Ответ HTTP 200 или таймаут перезагрузки сами по себе не подтверждают успех.
> Авторизацию и формат команды определяет профиль; автоматическое угадывание не допускается.

Сводная таблица по управлению различными семействами оборудования.

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
| **Canaan** | Avalon | Sleep / Normal | `TCP 4028` | None | `{"command": "ascset", "parameter": "0,softoff,1/0"}` | Returns success or requires physical check |
| **Canaan** | Avalon | LED On / Off | `TCP 4028` | None | `{"command": "ascset", "parameter": "0,led,1/0"}` | Returns success |
| **Elphapex** | DG-Series | Sleep / Normal | `POST /cgi-bin/luci/setworkmode.cgi`| None | `workmode: "-1000" / "0"` | HTTP 200 |
| **Jasminer** | X-Series | LED On / Off | `POST /cgi-bin/find_miner_on.cgi` | Digest `root:root` | None | HTTP 200 and "ok" in text |
