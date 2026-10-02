#!/bin/bash
# Рендер хостовых конфигов nginx из .env.
#
# Хостовой nginx деплоем не обновляется — этот скрипт и есть способ его
# обновить. Домены в репозитории не хранятся: в шаблонах только имена
# переменных, готовые конфиги существуют лишь на сервере.
#
# envsubst вызывается со списком переменных не случайно: без него он съел бы
# собственные переменные nginx ($host, $remote_addr и прочие).
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/karbi/app}
AVAILABLE=${AVAILABLE:-/etc/nginx/sites-available}
ENABLED=${ENABLED:-/etc/nginx/sites-enabled}
# bootstrap рендерится, но не включается: он нужен только пока у домена ещё
# нет сертификата, и включается руками на время выпуска.
SITES=${SITES:-"public admin"}

set -a
# shellcheck disable=SC1091
. "$APP_DIR/.env"
set +a

: "${PUBLIC_DOMAIN:?PUBLIC_DOMAIN must be set in .env}"
: "${ADMIN_DOMAIN:?ADMIN_DOMAIN must be set in .env}"
export PUBLIC_DOMAIN ADMIN_DOMAIN

# Формат лога мониторинга нужен шаблонам public и admin всегда, даже пока сам
# мониторинг не поставлен: без него nginx -t не пройдёт. Лог при этом пишется
# и ротируется, читать его просто некому.
CONF_D=${CONF_D:-/etc/nginx/conf.d}
LOGROTATE_D=${LOGROTATE_D:-/etc/logrotate.d}
install -m 644 "$APP_DIR/deploy/nginx/monitoring-log-format.conf" "$CONF_D/monitoring-log-format.conf"
install -m 644 "$APP_DIR/deploy/nginx/monitoring.logrotate" "$LOGROTATE_D/nginx-monitoring"

# Хост мониторинга включается, только когда домен задан и сертификат уже
# выпущен: иначе nginx -t упал бы на отсутствующем файле и утянул остальные.
MONITORING_DOMAIN=${MONITORING_DOMAIN:-}
export MONITORING_DOMAIN
CERTS=${CERTS:-/etc/letsencrypt/live}
if [ -n "$MONITORING_DOMAIN" ] && [ -e "$CERTS/$MONITORING_DOMAIN/fullchain.pem" ]; then
    SITES="$SITES monitoring"
elif [ -n "$MONITORING_DOMAIN" ]; then
    echo "monitoring: no certificate for $MONITORING_DOMAIN yet, host skipped" >&2
fi

for site in $SITES bootstrap; do
    template="$APP_DIR/deploy/nginx/host-$site.conf.template"
    test -f "$template" || { echo "no template for $site" >&2; exit 1; }
    envsubst '${PUBLIC_DOMAIN} ${ADMIN_DOMAIN} ${MONITORING_DOMAIN}' < "$template" > "$AVAILABLE/$site"
    chmod 644 "$AVAILABLE/$site"
done

for site in $SITES; do
    ln -sfn "$AVAILABLE/$site" "$ENABLED/$site"
done

nginx -t
echo "rendered: $SITES (+ bootstrap, not enabled)"
