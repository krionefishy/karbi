#!/bin/sh
# Локальный стенд «Контроль скриншотов ДБС»: up | down | reset | seed | logs | ps | compose ...
# Docker — отдельный профиль Colima (по умолчанию karbi-stand), чужие контексты не трогаются.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
profile=${STAND_COLIMA_PROFILE:-karbi-stand}
context=${STAND_DOCKER_CONTEXT:-colima-$profile}
env_file="$here/.env.stand"
credentials="$here/.credentials.txt"

compose() {
    docker --context "$context" compose --env-file "$env_file" -f "$root/deploy/compose.stand.yaml" -p karbi-stand "$@"
}

random() { # $1 — длина
    LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c "$1"
}

ensure_colima() {
    if docker --context "$context" info >/dev/null 2>&1; then
        return
    fi
    command -v colima >/dev/null || { echo "Нет docker-контекста $context и нет colima" >&2; exit 1; }
    # colima start переключает текущий docker-контекст на себя: возвращаем прежний, чтобы чужие
    # проекты на профиле default не начали ходить в стенд.
    previous=$(docker context show 2>/dev/null || echo "")
    colima start "$profile" --cpu "${STAND_CPU:-2}" --memory "${STAND_MEMORY_GB:-5}" --disk "${STAND_DISK_GB:-30}"
    [ -n "$previous" ] && docker context use "$previous" >/dev/null
}

ensure_env() {
    if [ ! -f "$env_file" ]; then
        umask 077
        cat >"$env_file" <<EOF
# Секреты локального стенда, сгенерированы $(date +%F). Не для сервера. Файл в .gitignore.
DB_PASSWORD=$(random 24)
REDIS_PASSWORD=$(random 24)
JWT_SECRET=$(random 48)
STAND_S3_ACCESS_KEY=stand$(random 12)
STAND_S3_SECRET_KEY=$(random 32)
SCREENSHOT_CONTROL_HMAC_KEY=$(random 48)
SCREENSHOT_CONTROL_MODE=shadow

# Настоящая модель Cloud.ru вместо поддельной: раскомментировать обе строки и выполнить just stand-up.
# LLM_BASE_URL=https://foundation-models.api.cloud.ru/v1
# FOUNDATION_MODELS_API_KEY=
EOF
        echo "Создан $env_file"
    fi
    if [ ! -f "$credentials" ]; then
        umask 077
        {
            echo "# Логины стенда: роль логин пароль. Файл в .gitignore."
            echo "admin stand-admin $(random 16)"
            echo "manager manager-anna $(random 16)"
            echo "manager manager-boris $(random 16)"
        } >"$credentials"
        echo "Создан $credentials"
    fi
}

seed() {
    compose run --rm --no-deps -e PYTHONPATH=/app -v "$here:/stand:ro" api python /stand/seed.py /stand/.credentials.txt
}

samples() {
    if [ ! -f "$here/samples/manifest.json" ] || [ "$(date +%F)" != "$(date -r "$here/samples/manifest.json" +%F)" ]; then
        # Даты на скринах относительные (вчера и раньше): набор пересобирается раз в день.
        (cd "$root" && uv run --quiet python deploy/stand/make_samples.py) || echo "Скрины не собраны: uv run python deploy/stand/make_samples.py" >&2
    fi
}

up() {
    ensure_colima
    ensure_env
    compose build
    compose up -d --wait --wait-timeout 300
    seed
    samples
    cat <<EOF

Стенд поднят:
  кабинет менеджера   http://localhost:8090/automations/wb-screenshot-control
  админка             http://admin.localhost:8090/admin/screenshots
  API                 http://localhost:8090/api/v1  (напрямую http://localhost:8091, OpenAPI /docs)
  S3-консоль (RustFS) http://localhost:19001  (ключи — STAND_S3_* в $env_file)
  логины              $credentials
  скриншоты           $here/samples
EOF
}

case "${1:-}" in
    up) up ;;
    down) compose down ;;
    reset)
        ensure_colima
        ensure_env
        compose down --volumes --remove-orphans
        up
        ;;
    init) ensure_colima; ensure_env ;;
    seed) seed ;;
    samples) samples ;;
    logs) shift; compose logs -f --tail 100 "$@" ;;
    ps) compose ps ;;
    compose) shift; compose "$@" ;;
    *) echo "usage: $0 up|down|reset|init|seed|samples|logs [service]|ps|compose ..." >&2; exit 2 ;;
esac
