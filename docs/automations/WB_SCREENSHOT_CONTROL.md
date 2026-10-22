# Контроль скриншотов ДБС

Статус: этап 1 — каркас. Схема базы, контракт API, топики и воркер есть; ручки отвечают
канонными заглушками (пустые списки, нулевые счётчики, конкретный скриншот — 404). Приём,
распознавание и решения — следующие этапы.

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
| `telegram_enabled` | `false` | второй релиз |
| `vision.model` (`VISION_MODEL`) | пусто | выбирается по пробному прогону |

Чтобы включить на проде: создать бакет и сервисный ключ в Cloud.ru Object Storage, положить
`S3_*` (и при отдельном бакете `SCREENSHOT_CONTROL_S3_BUCKET`), `SCREENSHOT_CONTROL_HMAC_KEY`
и `SCREENSHOT_CONTROL_MODE=shadow` в `.env`.
