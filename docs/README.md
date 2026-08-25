# Документация

Marketplace Auto — внутренняя платформа автоматизаций для продавцов Wildberries.
Оператор заводит селлера с ключом WB, подключает его к автоматизациям, а платформа
по расписанию собирает данные из WB и пишет в Telegram, когда есть о чём сказать.

## Карта

| Раздел | О чём |
| --- | --- |
| [GLOSSARY.md](GLOSSARY.md) | термины: селлер, подключение, прогон, артикул, слот, watermark |
| [architecture/](architecture/) | из чего состоит система и почему именно так |
| [business/](business/) | предметная область: селлеры, артикулы, уведомления, админка |
| [automations/](automations/) | по одному файлу на автоматизацию |
| [BACKUPS.md](BACKUPS.md), [KEY_ROTATION.md](KEY_ROTATION.md) | эксплуатационные процедуры |

### architecture

| Файл | О чём |
| --- | --- |
| [OVERVIEW.md](architecture/OVERVIEW.md) | контур целиком: процессы, хранилища, внешние системы |
| [MODULES.md](architecture/MODULES.md) | модульный монолит: слои и правила зависимостей |
| [DATA.md](architecture/DATA.md) | пять схем в одной базе, миграции, что где лежит |
| [EVENTS.md](architecture/EVENTS.md) | outbox, Kafka, идемпотентность, правило для S3 |
| [AUTH.md](architecture/AUTH.md) | вход оператора, токены, ключи селлеров |
| [NETWORK_AND_EGRESS.md](architecture/NETWORK_AND_EGRESS.md) | сети, откуда какой трафик уходит наружу |
| [WB_API.md](architecture/WB_API.md) | лимиты WB, троттлинг, ретраи |
| [WORKERS.md](architecture/WORKERS.md) | фоновые процессы, расписание, heartbeat, повторы |

### business

| Файл | О чём |
| --- | --- |
| [SELLERS.md](business/SELLERS.md) | реестр селлеров и участие в автоматизациях |
| [ARTICLE_LIFECYCLE.md](business/ARTICLE_LIFECYCLE.md) | чем опознаётся товар и что значат его состояния |
| [NOTIFICATIONS.md](business/NOTIFICATIONS.md) | боты, подписки, очередь и доставка |
| [ADMIN.md](business/ADMIN.md) | админка: сотрудники и боты |

## С чего начать

Новому человеку: [OVERVIEW](architecture/OVERVIEW.md) → [GLOSSARY](GLOSSARY.md) →
[business/SELLERS](business/SELLERS.md) → интересующая автоматизация.

## Соглашения

- **Пишем почему, а не что.** Что делает код, видно из кода; в доке — причина, из-за
  которой сделано именно так, и то, что сломается при «очевидном» упрощении.
- **Диаграммы — блоками, в mermaid.** GitHub рендерит их сам, картинки не нужны.
- **Ссылки на конкретные строки кода не ставим** — они протухают быстрее текста.
  Называем процессы, таблицы и эндпоинты, а не файлы и функции.
- **Новая автоматизация — свой файл** в [automations/](automations/) по
  [шаблону](automations/_TEMPLATE.md) и строка в каталоге. Шаблон — подсказка, что
  обычно спрашивают, а не обязательная форма.
