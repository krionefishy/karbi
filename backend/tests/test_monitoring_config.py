import json
import re

from backend.commands.monitoring_config import CONFIG_FILE, build_config, render


def test_monitoring_config_matches_api_routes() -> None:
    # Новый маршрут без записи в конфиге мониторинг свалил бы в «/[unknown]»
    # вместе со сканерами, и его ошибки потерялись бы среди чужих.
    assert CONFIG_FILE.read_text() == render(), "обновить: uv run python -m backend.commands.monitoring_config"


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
    config = json.loads(CONFIG_FILE.read_text())
    log_format = (CONFIG_FILE.parents[1] / "nginx/monitoring-log-format.conf").read_text()
    templates = [
        (CONFIG_FILE.parents[1] / f"nginx/host-{site}.conf.template").read_text() for site in ("public", "admin")
    ]

    # Сборщик молча отбрасывает строки с чужим application и не видит лог по
    # другому пути — расхождение выглядело бы как «запросов нет».
    assert f'"application":"{config["application"]}"' in log_format
    assert f'"environment":"{config["environment"]}"' in log_format
    assert all(f"access_log {config['log_path']} monitoring_json" in t for t in templates)
