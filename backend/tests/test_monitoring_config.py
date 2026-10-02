import re
from pathlib import Path

from backend.commands.monitoring_config import build_config

NGINX = Path(__file__).resolve().parents[2] / "deploy/nginx"


def test_monitoring_rules_normalise_identifiers() -> None:
    rules = build_config()["route_rules"]

    def route_for(path: str) -> str:
        return next(rule["route"] for rule in rules if re.fullmatch(rule["pattern"], path))

    seller = "3f2b1c1e-1111-2222-3333-444455556666"
    assert route_for(f"/wb/sellers/{seller}") == "/wb/sellers/{seller_id}"
    # Статический сосед параметра остаётся собой, а не превращается в идентификатор.
    assert (
        route_for(f"/wb/fbs-stocks/sellers/{seller}/groups/order") == "/wb/fbs-stocks/sellers/{seller_id}/groups/order"
    )
    assert route_for("/wp-login.php") == "/[unknown]"
    assert rules[-1]["pattern"] == ".*"


def test_monitoring_config_names_match_nginx_log_format() -> None:
    config = build_config()
    log_format = (NGINX / "monitoring-log-format.conf").read_text()
    templates = [(NGINX / f"host-{site}.conf.template").read_text() for site in ("public", "admin")]

    # Сборщик молча отбрасывает строки с чужим application и не видит лог по
    # другому пути — расхождение выглядело бы как «запросов нет».
    assert f'"application":"{config["application"]}"' in log_format
    assert f'"environment":"{config["environment"]}"' in log_format
    assert all(f"access_log {config['log_path']} monitoring_json" in t for t in templates)


def test_every_monitoring_service_has_a_health_check() -> None:
    # Раздел без health_url мониторинг показывает как «Unknown · не настроено».
    assert all(service.get("health_url") for service in build_config()["services"])
