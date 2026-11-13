# export_v1: observability и production gate

## Граница изменений

Этот слой наблюдает цепочку Marketplace Auto → ma-bridge → mp-sync, но не владеет
контрактом выгрузки и авторизацией. Роутер метрик подключён к тому же machine guard
KRB-39; публично или через обычный пользовательский JWT он недоступен. До clean
production-проверки bridge и открытия gate запуск consumer запрещён.

Активны ровно девять наборов: `sellers`, `products`, `orders`, `sales_rows`, `prices`,
`ad_campaigns`, `ad_stats_daily`, `stocks`, `ratings`. `seller_tax_rates` — legacy;
наблюдение с этим именем отвергается и не обновляет freshness.

## Подключение к KRB-39

`Application` создаёт один `ExportObservability` на процесс. После сериализации
страницы adapter KRB-39 вызывает `record_export_page(..., stage="sent")`.
ma-bridge и mp-sync публикуют те же имена метрик с тем же marketplace `channel`
(`wb`/`ozon`) и стадиями `accepted`/`written`. Имя компонента берётся из scrape target,
а не из label `channel`. Один UUID `transfer_id` передаётся между сервисами только в
служебном заголовке и структурированных логах; label метрик из него делать нельзя.

Страница KRB-39 может содержать несколько seller. Перед `observe` её надо разбить на
агрегаты по `(seller_id, channel)`: `rows` — число строк seller, `payload_bytes` — сумма
UTF-8 байтов уже сериализованных `item` этого seller, `pages=1` для каждого seller,
встреченного на странице. Общую обёртку ответа в seller-bytes не распределять. Для
`source_updated_at` брать максимальный `updated_at` группы, а в `cursor` передавать
выданный `next_cursor` — observer сохранит только необратимый fingerprint. Пустую
страницу учитывать в request/latency, но не создавать фиктивный seller label.

Роутер подключён в export_v1 рядом с его machine-token dependency:

```python
router.include_router(
    create_export_observability_router(observer_from_app_state, authorize=guard)
)
```

Endpoint должен оставаться доступным только с vm-bridge и по одному из двух токенов
ротации. Текущий отдельный monitoring Marketplace Auto читает nginx-логи и эти метрики
не собирает: scrape и rules надо подключить в observability-стеке ma-bridge после KRB-39.

## Данные и cardinality

Разрешённые labels: девять dataset, UUID seller, два marketplace channel и три stage. В labels и
логи запрещены названия кабинетов, артикулы, штрихкоды, payload, Bearer-токены,
тексты ошибок и raw cursor. Cursor хранится только как первые 16 hex SHA-256; это
достаточно, чтобы увидеть продвижение и повтор страницы, но нельзя восстановить ключ.
`error_code`, retry/rejection reason — только ограниченные машинные коды.

Unmapped seller — отдельный critical signal. UUID неизвестного seller не становится
label: в warning пишется только fingerprint, в metric — dataset/channel и число
нерешённых mapping. Это не даёт одному плохому источнику разогнать cardinality.

## Dashboard

Панели группируются по `dataset,seller,channel`:

| Панель | PromQL |
| --- | --- |
| rows rate | `sum by(dataset,seller,channel,stage)(rate(marketplace_auto_export_v1_rows_total[5m]))` |
| bytes rate | `sum by(dataset,seller,channel,stage)(rate(marketplace_auto_export_v1_bytes_total[5m]))` |
| pages rate | `sum by(dataset,seller,channel,stage)(rate(marketplace_auto_export_v1_pages_total[5m]))` |
| p95 transfer latency | `histogram_quantile(0.95, sum by(le,dataset,channel,stage)(rate(marketplace_auto_export_v1_latency_seconds_bucket[10m])))` |
| p95 exporter HTTP | `histogram_quantile(0.95, sum by(le,dataset,channel)(rate(marketplace_auto_export_v1_http_latency_seconds_bucket[10m])))` |
| source lag | `max by(dataset,seller,channel)(marketplace_auto_export_v1_source_lag_seconds)` |
| backlog | `max by(dataset,seller,channel)(marketplace_auto_export_v1_backlog_rows)` |
| retries | `sum by(dataset,channel,reason)(increase(marketplace_auto_export_v1_retries_total[1h]))` |
| 4xx/5xx/429 | `sum by(dataset,channel,status_class)(increase(marketplace_auto_export_v1_http_requests_total[1h]))` |
| rejected | `sum by(dataset,channel,stage,reason)(increase(marketplace_auto_export_v1_rejected_rows_total[1h]))` |
| sent→accepted | `abs(sum by(dataset,seller,channel)(marketplace_auto_export_v1_rows_total{stage="sent"}) - sum by(dataset,seller,channel)(marketplace_auto_export_v1_rows_total{stage="accepted"}))` |
| accepted→written | `abs(sum by(dataset,seller,channel)(marketplace_auto_export_v1_rows_total{stage="accepted"}) - sum by(dataset,seller,channel)(marketplace_auto_export_v1_rows_total{stage="written"}))` |
| unmapped seller | `max by(dataset,channel)(marketplace_auto_export_v1_unmapped_sellers)` |
| production gate | `min by(dataset,channel)(marketplace_auto_export_v1_production_consumer_gate_open)` |
| durable stages | `max by(dataset,channel,stage)(marketplace_auto_export_v1_production_stage_rows)` |
| report age | `max by(dataset,channel)(marketplace_auto_export_v1_production_report_age_seconds)` |

Alert rules лежат в `deploy/monitoring/export-v1-rules.yaml`. Пороги начальные:
остановка 15 минут, lag 30 минут, backlog 50 000 строк, любое устойчивое расхождение,
rejected или unmapped — инцидент. После первой недели production их уточнить по факту.

## Обязательный preflight перед consumer

После каждого полного sync ma-bridge отправляет `source=ma_bridge, stage=accepted`,
а mp-sync после commit своей БД отправляет `source=mp_sync, stage=written` в
`POST /export/v1/observability/production-report`. Оба используют отдельный
`EXPORT_REPORT_TOKEN`: export-token и bridge-token для этого не подходят.
Приложение принимает accepted только с vm-bridge, written только с vm-fin и само
считает sent по exporter queries. Каждый snapshot содержит ровно девять datasets
и только aggregate counts; частичные, stale и содержащие `seller_tax_rates`
отчёты отвергаются.

До открытия consumer gate по каждому из девяти datasets проверить:

1. `real_sellers > 0`; количество вывести отдельно по dataset.
2. `mock_sellers == 0` и `mock_rows == 0`. Детектор mock определяется в ma-bridge по
   его seed/fixture manifest, а не по именам или эвристике бизнес-данных.
3. `sent_rows == accepted_rows == written_rows`; accepted подтверждён БД ma-bridge,
   written — закоммиченными `sales.source_records` и `core.products` mp-sync.
4. `rejected_rows == 0`, `unmapped_sellers == 0`, `backlog_rows == 0`.
5. generation моложе 24 часов и `/export/v1/observability/status` отвечает 200.

Если хотя бы один пункт не выполнен, consumer не запускать. Mock rows сначала удалить
штатной процедурой ma-bridge с бэкапом; этот runbook не разрешает удаление данных.

## Разбор инцидента

1. Зафиксировать dataset/seller/channel, время и `transfer_id` из безопасного лога.
2. При 429 остановить агрессивные повторы, проверить `Retry-After` и retry budget.
3. При 5xx/сети cursor не продвигать; повторить ту же страницу идемпотентно.
4. При `sent > accepted` смотреть rejected/unmapped и контракт schema_version.
5. При `accepted > written` смотреть backlog и транзакцию mp-sync; accepted нельзя
   считать written до commit.
6. Raw payload, cursor и токены в тикет/чат не копировать. Для корреляции достаточно
   `transfer_id`, dataset, UUID seller, channel, counts и cursor fingerprint.
