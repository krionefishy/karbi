# Контроль скриншотов ДБС

Статус: приём, конвейер (распознавание и решение), хранилище и боевое API менеджера,
расширения и админа готовы и покрыты тестами на PostgreSQL и Redis; S3 в тестах — фейк.
Telegram — второй релиз. На живом S3 и живой модели распознавания не проверялось.

## Зачем

Менеджеры, которые разбирают плохие отзывы WB, подтверждают результат скриншотом с подписью
одного из трёх исходов: «полож» (отзыв положительный), «доп» (договорились, отзыв изменён на
положительный), «удален» (отзыв удалён). На принятых скриншотах держится KPI менеджера,
поэтому нужна защита от повторов и подделок, а спорное должен видеть администратор.

Единица работы — скриншот с подписью: жизненного цикла кейса нет, подпись против содержимого
не сверяется. Сверки с WB тоже нет; задел под неё — нормализованные `chat_id`, время и артикул
отдельными колонками `submissions`.

Технические имена: модуль `wb_screenshot_control`, id автоматизации `wb-screenshot-control`,
схема `wb_screenshot_control` (ревизии `sc…`), воркер `wb-screenshot-control-worker`. В
каталоге это инструмент (`kind="tool"`): селлеров не подключают.

## Каналы

| Канал | Вход | Авторизация |
| --- | --- | --- |
| Веб | `POST /api/v1/wb/screenshot-control/submissions`, multipart | JWT сотрудника |
| Расширение Chrome | `…/extension/*`, URL вкладки из `tab.url` | токен установки `mas_…`, выдаётся по коду сопряжения из 8 символов; обмен кода под жёстким лимитом на IP |
| Telegram | второй релиз | флаг `telegram_enabled=false`; таблицы привязки уже есть |

Админ-панель — `/api/v1/admin/screenshot-control/*`, только `CurrentAdmin`, на рабочем
домене префикс закрыт nginx. Контракт целиком — `presentation/http/schemas.py`, JSON-схема —
`just openapi --tag wb-screenshot-control`. Машинный код ошибки — заголовок `X-Error-Code`
(новая для проекта конвенция), текст — как везде, в `detail`.

## Конвейер

Transactional outbox и два консьюмера вместо очереди-таблицы: приём одной транзакцией пишет
`files`, `submissions` (`stage=processing`) и события `screenshot.submitted` и
`storage.s3.wb-screenshot-control.upload`; распознавание (VLM + OCR) пишет `extractions` и
`screenshot.extracted`; решение идёт по одному в порядке приёма — все `extracted` одним ключом
партиции — и пишет вердикт, журнал `verdict_events` и `screenshot.verdict-ready`. Outbox и
inbox общие (`wb_core`).

| Топик | Что |
| --- | --- |
| `wb.screenshot-control.screenshot.submitted` | скрин принят, ждёт распознавания |
| `wb.screenshot-control.screenshot.extracted` | поля прочитаны, ждёт решения |
| `wb.screenshot-control.screenshot.verdict-ready` | вердикт готов |
| `storage.s3.wb-screenshot-control.upload` / `.delete` | запись и удаление в S3 |

## Вердикты

| Вердикт | Что значит | В KPI |
| --- | --- | --- |
| `accepted` | засчитан | да |
| `duplicate` | повтор уже загруженного кейса | нет |
| `needs_review` | спорно, ждёт администратора | нет, пока не одобрен |
| `rejected` | не скриншот, не читается или нет исхода | нет |

Менеджер видит статус (`awaiting_outcome`, `processing` или вердикт) и узкий словарь причин;
пороги и детали эвристик не раскрываются. Полный словарь причин — `ReasonCode` в контракте,
у администратора. В режиме `shadow` цифры KPI помечены как предварительные.

## Хранение

Скриншоты лежат в S3 Cloud.ru. Модуль сам в S3 не пишет: запись и удаление — только событиями
в Kafka, их выполняют консьюмеры воркера; в событиях нет байтов. До выгрузки файл лежит в томе
`screenshots_staging` (`/data/screenshots-staging`, общий у api и воркера). Состояние каждого
объекта — таблица `storage_objects` (`staged → uploading → stored → delete_requested → deleted`,
любое → `failed`). Чтение — presigned GET напрямую из S3 на 5 минут: оригинал как вложение,
миниатюра inline.

Ключи по содержимому: `dbs-screenshots/orig/{sha[0:2]}/{sha256}.{ext}`,
`dbs-screenshots/thumb/{sha[0:2]}/{sha256}.jpg`.

| Что | Срок |
| --- | --- |
| Оригинал | 365 дней |
| Миниатюра | 365 дней |
| Поля, вердикты, журнал | 3 года |

Имя покупателя в открытом виде не хранится: только HMAC (`SCREENSHOT_CONTROL_HMAC_KEY`).

## API

Префикс `/api/v1`. Контракт — `presentation/http/schemas.py`; машинный код ошибки — `X-Error-Code`.
Чужой скриншот для менеджера неотличим от несуществующего: 404 `not_found`.

### Менеджер (JWT сотрудника)

| Ручка | Что |
| --- | --- |
| `POST /wb/screenshot-control/submissions` | multipart `file`, `client_submission_id`, `outcome?`; 201 новая, 200 повтор того же id; 409 `idempotency_conflict` (тот же id, другой файл), 413/415/422 по файлу, 503 `storage_full` |
| `GET /wb/screenshot-control/submissions` | свои, свежие первыми; `status`, `outcome`, `from`/`to` (даты МСК, без дат — всё), `cursor`, `limit ≤ 100` |
| `GET …/submissions/{id}` | карточка: статус, узкая причина и фраза, когда загружен оригинал дубля (чей — нет), прочитанные поля (от chatId — 6 последних символов), `thumb_url` |
| `PATCH …/submissions/{id}/outcome` | пока вердикта нет; иначе 409 `outcome_locked`. Исключение — отказ `no_outcome` моложе `outcome_ttl_hours`: исход принимается, решение пересчитывается (stage=extracted, новое `screenshot.extracted`) |
| `GET …/submissions/{id}/image-url` | presigned GET миниатюры; нет в S3 — `available=false` |
| `GET /wb/screenshot-control/summary` | KPI за период (по умолчанию сегодня МСК): принятые по исходам, на проверке, ждут исхода, в работе, дубли, отказы; `preliminary` в shadow и раньше `kpi_from`; > 366 дней — 422 `period_too_long` |
| `GET /wb/screenshot-control/config` | режим, исходы, лимиты файла, ссылка на расширение; отвечает и при `mode=off` |
| `POST …/extension/pairing-code`, `GET/DELETE …/extension/installs[/{id}]` | код сопряжения (новый гасит прежний), свои установки; чужая установка — 404 |
| `…/telegram*` | `link-token` — 503 `telegram_disabled` до второго релиза; статус и отвязка работают |

### Расширение (`Authorization: Bearer mas_…`, `X-Extension-Version`)

`POST /pair` (без токена, код из 8 символов Crockford) → `{token, username, min_version,
allowed_url_prefixes}`; код гасится атомарно, повтор — 404 `code_invalid`. Тот же `install_id`
отзывает прежнюю установку; живых установок у сотрудника не больше `max_extension_installs` (3),
лишние старые отзываются. Токен — `mas_` + 32 байта, в базе только sha256.

На каждом запросе: установка не отозвана (401 `install_revoked`), простой не дольше
`extension_token_idle_days` (иначе тоже 401 `install_revoked`), сотрудник активен (403
`user_blocked` сразу после блокировки), версия не ниже `extension_min_version` (426
`upgrade_required`). Ручки: `GET /me`, `POST /submissions` (исход обязателен — 422
`outcome_required`; URL вкладки, время снимка, часовой пояс, viewport и dpr пишутся в
`client_meta`), `GET /submissions`, `GET /submissions/{id}`, `GET /summary`.

### Админ (`/api/v1/admin/screenshot-control/*`, только `CurrentAdmin`; менеджер — 403)

| Ручка | Что |
| --- | --- |
| `GET /queue` | `kind=review\|duplicates\|audit\|all`, `reason`, `user_id`, `from`/`to`, `cursor`; очереди — по подозрительности (high → medium → low), затем старые первыми; `all` — свежие первыми; `counts` |
| `GET /submissions/{id}` | поля полностью (без имени покупателя, только `buyer_name_seen`), согласие VLM/OCR, причины, совпадения с чужими скринами и их менеджерами, файл, распознавание, журнал |
| `GET /submissions/{id}/image-url` | `kind=thumb` inline, `kind=original` вложением со своим типом |
| `POST /submissions/{id}/resolve` | решения по таблице ниже; журнал `verdict_events` с actor `admin:<id>`, событие `verdict-ready` с actor=admin |
| `POST /submissions/{id}/reprocess` | 202: вердикт в журнал и сброс, stage=processing, новое `screenshot.submitted` |
| `DELETE /submissions/{id}/file` | 202: оригинал и миниатюра файла (у всех его скринов) → `delete_requested` и событие удаления в S3; без S3 — стирается staging, `deleted` |
| `GET /managers` | по менеджерам за период: всего, принято по исходам, дубли, проверка, отказы, доли, флаги (`high_duplicate_rate` > 10 %, `high_review_rate` > 30 %, `high_reject_rate` > 30 % — все при ≥ 20 скринах; `upload_burst`) |
| `GET /managers/export` | CSV для HR/KPI: менеджер × день МСК, колонки `ManagerDayRow`, разделитель `;`, UTF-8 с BOM, `preliminary` по дню |
| `GET /pipeline` | очередь по стадиям, возраст старейшего, p50/p95 приём → вердикт за сутки, доля ошибок VLM за час, токены и отказы распознавания за день, объекты хранилища по состояниям, заполненность staging, каналы за день |
| `GET /config` | действующая конфигурация без секретов |

| Сейчас | Решение | Станет | Условие |
| --- | --- | --- | --- |
| needs_review | approve | accepted | нет другого принятого с тем же `text_key` и чатом, иначе 409 `exact_key_conflict` |
| needs_review | decline | rejected | нужен `note` (422 `note_required`) |
| needs_review | duplicate_of | duplicate | цель существует и не сам скрин (422 `duplicate_target_invalid`); ссылка — на корень цепочки |
| accepted | decline / duplicate_of | rejected / duplicate | нужен `note`; открытый аудит закрывается |
| accepted | audit_ok | accepted | только при открытом аудите |
| duplicate | not_duplicate | accepted | тот же запрет, что у approve |

Остальное — 409 `transition_not_allowed`. `outcome` в решении меняет исход (источник `admin`).
`corrections` (правка распознанных полей) отложены: поле в запросе отклоняется 422.

### Лимиты

- На IP: общий лимитер проекта (`RATE_LIMIT_REQUESTS`, 30/мин на проде) душил бы офис за одним
  адресом, поэтому `…/submissions*` и `…/extension/*` идут по своему бюджету
  `screenshot_control.ip_requests_per_minute` (300/мин, ключи `rate_limit:ip-screenshots`).
  Совсем лимит не снят: multipart читается до авторизации.
- `POST …/extension/pair` — под общим лимитом и под жёстким своим: `pair_attempts_per_window` за
  `pair_window_seconds` с IP (10 за 10 мин), 429 с `Retry-After`.
- На сотрудника (веб и расширение вместе, ключи `sc:user:*`): загрузки `uploads_per_minute` (20) и
  `uploads_per_hour` (120), опрос (списки, карточки, сводка, ссылки) `status_polls_per_minute`
  (120), коды сопряжения — как попытки обмена. 429 `rate_limited` с `Retry-After`.
- Недоступный Redis — 503 `limiter_unavailable`, как у общего лимитера (fail-closed).

### Без S3

S3 выключен (`S3_ENABLED=false`) или бакет не задан — приём работает, файлы копятся в staging,
события выгрузки не публикуются. Ссылки на картинки не выдаются: `thumb_url=null`,
`image-url` отвечает `available=false`; удаление файла админом стирает staging сразу.

## Настройки

Секция `screenshot_control` и отдельная `vision` (модель распознавания, не трогает текстовую
`llm` бота поддержки).

| Параметр | По умолчанию | Что |
| --- | --- | --- |
| `mode` (`SCREENSHOT_CONTROL_MODE`) | `shadow` в конфиге, `off` в compose | `off` — модуль выключен, приём отвечает 503 `module_off`; `shadow` — вердикты пишутся, KPI предварительный; `enforce` — боевой |
| `kpi_from` | пусто | дата начала боевого KPI |
| `hmac_key` (`SCREENSHOT_CONTROL_HMAC_KEY`) | — | обязателен в проде при `mode != off`, 32+ символов |
| `s3_bucket` (`SCREENSHOT_CONTROL_S3_BUCKET`) | пусто | отдельный бакет под ПДн; пусто — общий `s3.bucket`; доступ и регион — общие `S3_*` |
| `staging_root` | `/data/screenshots-staging` | том до выгрузки в S3 |
| `retention_original_days`, `retention_thumb_days` | 365, 365 | сроки файлов |
| `thresholds` | `text_dup` 0.90, `text_maybe` 0.75, `min_text_len` 25, … | пороги дедупа, калибруются до `enforce` |
| `pair_attempts_per_window`, `pair_window_seconds` | 10 за 600 с | лимит обмена кода сопряжения с одного IP |
| `ip_requests_per_minute` | 300 | бюджет на IP для приёма и расширения вместо общего лимитера |
| `uploads_per_minute`, `uploads_per_hour`, `status_polls_per_minute` | 20, 120, 120 | лимиты на сотрудника |
| `max_extension_installs`, `extension_token_idle_days` | 3, 60 | живых установок на сотрудника, срок простоя токена |
| `image_url_ttl_seconds` | 300 | срок presigned-ссылки |
| `telegram_enabled` | `false` | второй релиз |
| `vision.model` (`VISION_MODEL`) | пусто | выбирается по пробному прогону |

Чтобы включить на проде: создать бакет и сервисный ключ в Cloud.ru Object Storage, положить
`S3_*` (и при отдельном бакете `SCREENSHOT_CONTROL_S3_BUCKET`), `SCREENSHOT_CONTROL_HMAC_KEY`
и `SCREENSHOT_CONTROL_MODE=shadow` в `.env`.
