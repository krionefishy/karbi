#!/bin/bash
# Обновление конфига внешнего мониторинга после выкладки.
#
# Конфиг печатает сам API (backend/commands/monitoring_config.py), поэтому он
# всегда соответствует выкаченным маршрутам. Если он не изменился или мониторинг
# на машине не установлен, скрипт ничего не трогает.
#
# Сборщик мониторинга отказывается стартовать с новым конфигом, пока у него
# есть неотправленные данные, собранные по старому. Отправляет их только он
# сам, поэтому при таком отказе возвращаем прежний файл, даём ему дослать и
# пробуем снова. Невалидный для мониторинга конфиг откатывается так же, и
# скрипт завершается ошибкой: мониторинг остаётся на прежнем, рабочем.
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/karbi/app}
MONITORING_ETC=${MONITORING_ETC:-/etc/monitoring-marketplace-auto}
MONITORING_DEPLOY=${MONITORING_DEPLOY:-/opt/monitoring/monitor-service/deploy}
ATTEMPTS=${ATTEMPTS:-3}
SETTLE_SECONDS=${SETTLE_SECONDS:-12}
SERVICES="collector worker api"

target="$MONITORING_ETC/application.json"

if ! sudo test -f "$MONITORING_ETC/runtime.env"; then
    echo "monitoring: не установлен, конфиг не обновляется"
    exit 0
fi

app_compose() {
    sudo docker compose --env-file "$APP_DIR/.env" -f "$APP_DIR/deploy/compose.yaml" "$@"
}

monitoring_compose() {
    sudo docker compose --env-file "$MONITORING_ETC/runtime.env" \
        -f "$MONITORING_DEPLOY/docker-compose.yaml" \
        -f "$MONITORING_DEPLOY/compose.application-network.yaml" "$@"
}

# Контейнер перезапускается сам (restart: unless-stopped), поэтому «running»
# сразу после рестарта ничего не значит: смотрим, что он продержался паузу
# и ни разу за неё не перезапустился.
healthy() {
    local service id state restarts_before restarts_after
    declare -A before=()
    for service in $SERVICES; do
        id=$(monitoring_compose ps -q "$service")
        test -n "$id" || return 1
        before[$service]=$(sudo docker inspect --format '{{.RestartCount}}' "$id")
    done
    sleep "$SETTLE_SECONDS"
    for service in $SERVICES; do
        id=$(monitoring_compose ps -q "$service")
        test -n "$id" || return 1
        state=$(sudo docker inspect --format '{{.State.Status}}' "$id")
        restarts_before=${before[$service]}
        restarts_after=$(sudo docker inspect --format '{{.RestartCount}}' "$id")
        if [ "$state" != "running" ] || [ "$restarts_after" != "$restarts_before" ]; then
            echo "monitoring: $service не поднялся ($state, перезапусков $restarts_before -> $restarts_after)" >&2
            monitoring_compose logs --tail 15 "$service" >&2 || true
            return 1
        fi
    done
}

apply() {
    sudo install -m 0644 "$1" "$target"
    monitoring_compose restart $SERVICES >/dev/null
}

new=$(mktemp)
old=$(mktemp)
trap 'rm -f "$new" "$old"' EXIT

app_compose exec -T api python -m backend.commands.monitoring_config > "$new"
python3 -c 'import json, sys; json.load(open(sys.argv[1]))["route_rules"]' "$new"

if sudo cmp -s "$new" "$target"; then
    echo "monitoring: конфиг не изменился"
    exit 0
fi

sudo cat "$target" > "$old" 2>/dev/null || true

for attempt in $(seq 1 "$ATTEMPTS"); do
    apply "$new"
    if healthy; then
        echo "monitoring: конфиг обновлён (попытка $attempt)"
        exit 0
    fi
    if [ -s "$old" ]; then
        echo "monitoring: возвращаю прежний конфиг" >&2
        apply "$old"
        # Пауза нужна сборщику, чтобы дослать накопленное по старому конфигу.
        sleep 30
    fi
done

echo "monitoring: новый конфиг не принят за $ATTEMPTS попытки, оставлен прежний" >&2
exit 1
