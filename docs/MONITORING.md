# Мониторинг

Отдельный стек [krionefishy/monitoring](https://github.com/krionefishy/monitoring)
`v2.0.0` на том же сервере: графики запросов, коды ответов, задержки, ошибки по
ручкам и доступность API и фронта. Приложение о нём не знает — мониторинг читает
лог хостового nginx и сам опрашивает health. Своя база, свои пользователи, свой
compose-проект `monitoring_marketplace_auto`; в `deploy/compose.yaml` его нет.

## Что видно и что нет

- В отчёты попадают запросы `/api/v1/**` с основного домена и с админки.
  Статика фронта и сканеры вне `/api/v1` отбрасываются, `/api/v1/health/*` тоже.
- Несуществующие пути под `/api/v1` сводятся в одну строку `/[unknown]`.
- Разделы — по префиксу автоматизации (`/wb/returns` → «Возвраты»). Первый
  сегмент пути у нас почти всегда `wb`, поэтому встроенная группировка
  мониторинга по нему бесполезна.
- Доступность: API (`/api/v1/health/ready`, база и Redis) и фронт. У воркеров
  HTTP нет, мониторинг их не видит — за ними следит healthcheck compose.
- MCP — отдельное приложение, ему нужен свой экземпляр.
- Бэкенд `X-Request-ID` пока не пишет в свои логи: по идентификатору из
  мониторинга ответ находится в логе nginx, но не в логе приложения.

## Что лежит в репозитории

| Файл | Назначение |
| --- | --- |
| `deploy/monitoring/application.json` | Конфиг мониторинга: разделы, правила нормализации идентификаторов, health. Собирается из маршрутов API |
| `backend/commands/monitoring_config.py` | Сборка конфига; `just monitoring-config` |
| `deploy/nginx/monitoring-log-format.conf` | Формат JSON-лога, уезжает в `/etc/nginx/conf.d` |
| `deploy/nginx/monitoring.logrotate` | Ротация лога, уезжает в `/etc/logrotate.d/nginx-monitoring` |
| `deploy/nginx/host-monitoring.conf.template` | Хост поддомена мониторинга |
| `deploy/nginx/host-{public,admin}.conf.template` | Второй `access_log` и `X-Request-ID` |

После добавления или переименования маршрута API — `just monitoring-config`;
тест `test_monitoring_config.py` падает, если конфиг отстал. На сервере новый
конфиг начинает действовать после перезапуска `collector` и `worker`
(см. «Обновление конфига»).

Секреты, `runtime.env` и checkout мониторинга живут только на сервере.

## Установка на сервере

Перед началом: у ВМ есть запас памяти (стек занимает 400–500 МБ в покое, лимиты
в его compose — до 3 ГБ суммарно), и запись A поддомена указывает на сервер.

### 1. Лог nginx

Работает и без самого мониторинга: лог пишется и ротируется.

```bash
sudo bash /opt/karbi/app/deploy/nginx/render-hosts.sh
sudo systemctl reload nginx
curl -s -o /dev/null https://marketplace-auto.ru/api/v1/health/live
sleep 6 && sudo tail -n 1 /var/log/nginx/marketplace-auto-monitoring.json
```

Скрипт сам прогоняет `nginx -t` и до reload ничего не меняет в работающем nginx.

### 2. Сертификат и хост поддомена

В `/opt/karbi/app/.env` добавить `MONITORING_DOMAIN=monitoring.marketplace-auto.ru`.
Порт 80 для нового имени отвечает хостом по умолчанию, поэтому для выпуска
сертификата нужен временный хост с `acme-challenge` — так же, как выпускались
три остальных, — затем:

```bash
sudo certbot certonly --webroot -w /var/www/certbot -d monitoring.marketplace-auto.ru
sudo bash /opt/karbi/app/deploy/nginx/render-hosts.sh
sudo systemctl reload nginx
```

Пока сертификата нет, `render-hosts.sh` хост мониторинга пропускает и говорит об этом.

### 3. Стек мониторинга

```bash
sudo git clone https://github.com/krionefishy/monitoring.git /opt/monitoring
sudo git -C /opt/monitoring checkout v2.0.0
sudo install -d -m 0755 /etc/monitoring-marketplace-auto
sudo install -m 0644 /opt/karbi/app/deploy/monitoring/application.json /etc/monitoring-marketplace-auto/application.json
sudo install -m 0600 /opt/monitoring/monitor-service/deploy/.env.example /etc/monitoring-marketplace-auto/runtime.env
```

В `runtime.env`:

```dotenv
COMPOSE_PROJECT_NAME=monitoring_marketplace_auto
MONITOR_INSTANCE_ID=marketplace-auto-production
MONITOR_SECRET_KEY=        # python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
POSTGRES_PASSWORD=         # то же, отдельное значение
POSTGRES_READ_PASSWORD=    # то же, отдельное значение
MONITOR_CONFIG_FILE=/etc/monitoring-marketplace-auto/application.json
MONITOR_PORT=8088
MONITOR_PUBLIC_ORIGIN=https://monitoring.marketplace-auto.ru
MONITOR_COOKIE_SECURE=true
NGINX_LOG_DIRECTORY=/var/log/nginx
NGINX_LOG_GID=4
MONITOR_APP_NETWORK=karbi_proxy
MONITOR_TRUSTED_PROXIES=127.0.0.1,::1
```

`NGINX_LOG_GID=4` — группа `adm`, которой принадлежат логи nginx на сервере.
`karbi_proxy` — сеть, в которой видны `karbi-api-1` и `karbi-frontend-1`; к ней
подключается только `worker` мониторинга.

```bash
dc() {
  sudo docker compose --env-file /etc/monitoring-marketplace-auto/runtime.env \
    -f /opt/monitoring/monitor-service/deploy/docker-compose.yaml \
    -f /opt/monitoring/monitor-service/deploy/compose.application-network.yaml "$@"
}
dc up -d --build
dc ps -a          # init — Exited (0), остальные работают
```

Сборка образа тянет Node и собирает фронт мониторинга: на время сборки нужно
около 1 ГБ памяти и 2 ГБ диска. Место освобождает `sudo docker builder prune`.

### 4. Доверенный прокси

Адрес, с которого хостовой nginx приходит в контейнер `api`, — шлюз его сети:

```bash
sudo docker inspect "$(dc ps -q api)" \
  --format '{{range $name, $net := .NetworkSettings.Networks}}{{$name}} gateway={{$net.Gateway}}{{println}}{{end}}'
```

Шлюз сети `…_web` записать в `MONITOR_TRUSTED_PROXIES` вместо loopback и
пересоздать `api`: `dc up -d --no-deps --force-recreate api`. Без этого лимит
попыток входа общий на всех.

### 5. Пользователи

```bash
dc exec api python -m app.v2.cli create-user admin
dc exec api python -m app.v2.cli create-user operator --role viewer
```

Пароль (от 12 символов) запрашивается интерактивно. Регистрации через веб нет.

## Обновление конфига

Мониторинг не даёт сменить конфиг, пока в сборщике есть неотправленные данные.

```bash
sudo install -m 0644 /opt/karbi/app/deploy/monitoring/application.json /etc/monitoring-marketplace-auto/application.json
dc restart collector worker api
dc logs --tail 50 collector
```

Если `collector` падает с «Drain pending batches…» — дождаться, пока он отправит
накопленное на старом конфиге (вернуть прежний файл, подождать минуту), и повторить.

## Что беречь

Том `primary_v2`, `runtime.env` (секрет экземпляра привязан к базе) и
`application.json`. `docker compose down -v` не запускать. Read-база и Redis
восстанавливаются из primary: `dc run --rm --no-deps api python -m app.v2.cli rebuild-read`.
