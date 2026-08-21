# Бэкапы Postgres

## Что бэкапится и куда

Сервис `pg-backup` в `deploy/compose.yaml` (образ `postgres:16-alpine`, скрипт
`deploy/pg-backup.sh`) раз в сутки снимает полный дамп базы `karbi` командой
`pg_dump --format=custom` — в один дамп попадают все схемы: `platform`,
`wb_core`, `wb_reviews`, `wb_turnover`, `notifications`.

- Дампы лежат в именованном volume `postgres_backups`, смонтированном в
  контейнер как `/backups`, файлы вида `karbi-20260820T031500Z.dump`.
- Хранится 14 последних суточных копий (`BACKUP_RETENTION`, задаётся в `.env`),
  интервал — `BACKUP_INTERVAL_SECONDS` (по умолчанию 86400).
- Дамп пишется во временный файл и переименовывается атомарно: файла
  `*.dump.partial` в ротацию и restore брать нельзя.
- Healthcheck сервиса краснеет, если свежайший дамп старше ~25 часов —
  `docker compose ps` (или `just prod-status`) это покажет.

Ручной дамп в любой момент: `just prod-backup`. Список копий: `just prod-backup-list`.

## ВАЖНО: копии должны покидать сервер

Volume `postgres_backups` живёт на том же диске, что и сама база. Это защищает
от «уронили таблицу миграцией», но не от смерти диска или сервера. Обязательно
настройте на хосте выгрузку каталога volume наружу (rsync/restic/objectstore на
другой сервер или в S3) — например, cron-задачей поверх
`docker run --rm -v karbi_postgres_backups:/backups ...`. Настройка внешней
выгрузки в этот репозиторий не входит, но без неё бэкап нельзя считать бэкапом.

## Восстановление

1. Остановите всё, что пишет в базу (API и воркеры), базу и pg-backup оставьте:

   ```sh
   sudo docker compose --env-file .env -f deploy/compose.yaml stop \
     api wb-reviews-worker wb-turnover-worker notifications-worker outbox-publisher
   ```

2. Выберите дамп: `just prod-backup-list`.

3. Восстановите поверх текущей базы (`--clean --if-exists` сначала удаляет
   объекты, затем создаёт заново):

   ```sh
   just prod-restore karbi-20260820T031500Z.dump
   ```

   Это то же самое, что вручную:

   ```sh
   sudo docker compose --env-file .env -f deploy/compose.yaml exec -T pg-backup \
     sh -c 'pg_restore --clean --if-exists --no-owner --dbname="$PGDATABASE" /backups/karbi-20260820T031500Z.dump'
   ```

4. Поднимите остальные сервисы: `just prod-up`. Миграции сверят head при старте
   контейнера `migrate`.

Если база разрушена целиком (новый сервер, пустой volume) — сначала поднимите
только `db`, создайте пустую базу с тем же именем (entrypoint Postgres сделает
это сам по `POSTGRES_DB`), затем выполните шаги 2–4.

## Проверка восстановимости

Бэкап, который ни разу не восстанавливали, — это лотерея. Раз в квартал:

1. На любой машине с docker поднимите чистый Postgres 16:
   `docker run -d --name restore-test -e POSTGRES_PASSWORD=test postgres:16-alpine`.
2. Скопируйте свежий дамп с сервера и прогоните
   `pg_restore --clean --if-exists --no-owner -h ... -U postgres -d postgres karbi-*.dump`.
3. Убедитесь, что restore завершился без ошибок и ключевые таблицы не пустые:
   `SELECT count(*) FROM wb_core.sellers;`, `SELECT count(*) FROM notifications.bots;`.
4. Удалите тестовый контейнер.
