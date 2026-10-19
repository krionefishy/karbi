import json
import re
import sys
from typing import Any

from fastapi import FastAPI

from backend.app.factory import create_app

BASE_PATH = "/api/v1"
LOG_PATH = "/var/log/nginx/marketplace-auto-monitoring.json"

PARAM_PATTERNS = {
    "uuid": "[0-9a-fA-F-]{36}",
    "integer": "[0-9]+",
}
ANY_SEGMENT = "[^/]+"

SERVICE_NAMES = {
    "/admin": "Админка",
    "/auth": "Вход",
    "/automations": "Каталог автоматизаций",
    "/fin-reports": "Финансовые отчёты",
    "/onec": "1С",
    "/wb/box-stickers": "Стикеры коробов",
    "/wb/card-checklist": "Чек-лист карточек",
    "/wb/fbs": "Распределение FBS",
    "/wb/fbs-penalties": "Штрафы FBS",
    "/wb/fbs-stocks": "Остатки FBS",
    "/wb/podsort": "Подсорт",
    "/wb/returns": "Возвраты",
    "/wb/review-chats": "Чаты после отзыва",
    "/wb/support-bot": "Бот поддержки в чатах",
    "/wb/reviews": "Отзывы",
    "/wb/sellers": "Селлеры",
    "/wb/turnover": "Оборачиваемость",
}


def _api_paths(app: FastAPI) -> dict[str, dict[str, str]]:
    """Путь после BASE_PATH -> типы его параметров. Скрытые из схемы маршруты тоже нужны."""
    paths: dict[str, dict[str, str]] = {}
    for route in _walk(app.routes):
        path = getattr(route, "path", "")
        if not path.startswith(BASE_PATH + "/"):
            continue
        dependant = getattr(route, "dependant", None)
        params = {
            field.name: "uuid"
            if "UUID" in str(field.field_info.annotation)
            else "integer"
            if field.field_info.annotation is int
            else "string"
            for field in (dependant.path_params if dependant else [])
        }
        paths.setdefault(path[len(BASE_PATH) :], {}).update(params)
    return paths


def _walk(routes: Any) -> Any:
    for route in routes:
        contexts = getattr(route, "effective_route_contexts", None)
        if contexts is not None:
            yield from contexts()
        else:
            yield route


def _service_prefix(path: str) -> str:
    parts = path.strip("/").split("/")
    return "/" + "/".join(parts[:2] if parts[0] == "wb" else parts[:1])


def _rule(path: str, params: dict[str, str]) -> dict[str, str]:
    pattern = re.sub(
        r"\\\{(\w+)\\\}",
        lambda match: PARAM_PATTERNS.get(params.get(match.group(1), ""), ANY_SEGMENT),
        re.escape(path),
    )
    return {"pattern": pattern, "route": path}


def build_config(app: FastAPI | None = None) -> dict[str, Any]:
    paths = _api_paths(app or create_app())
    excluded = sorted(path for path in paths if path.startswith("/health/"))
    routed = {path: params for path, params in paths.items() if path not in excluded}

    ordered = sorted(
        routed.items(),
        key=lambda item: (sum(kind == "string" for kind in item[1].values()), item[0]),
    )
    rules = [_rule(path, params) for path, params in ordered]
    rules.append({"pattern": ".*", "route": "/[unknown]"})

    prefixes = sorted({_service_prefix(path) for path in routed})
    missing = [prefix for prefix in prefixes if prefix not in SERVICE_NAMES]
    if missing:
        raise SystemExit(f"нет названия раздела в SERVICE_NAMES: {', '.join(missing)}")

    api_health: dict[str, Any] = {
        "health_url": f"http://karbi-api-1:8000{BASE_PATH}/health/ready",
        "health_body": {"database": True, "redis": True},
    }
    services: list[dict[str, Any]] = [
        {"id": "api", "name": "API", "prefix": "/", **api_health},
        {
            "id": "frontend",
            "name": "Фронтенд",
            "prefix": "/-/frontend",
            "health_url": "http://karbi-frontend-1/health",
        },
    ]
    services += [
        {
            "id": prefix.strip("/").replace("/", "-"),
            "name": SERVICE_NAMES[prefix],
            "prefix": prefix,
            **api_health,
        }
        for prefix in prefixes
    ]

    return {
        "application": "marketplace-auto",
        "environment": "production",
        "topology": "direct",
        "base_path": BASE_PATH,
        "log_path": LOG_PATH,
        "rotated_glob": LOG_PATH + ".[0-9]",
        "exclude_paths": [BASE_PATH + path for path in excluded],
        "max_routes": 2000,
        "route_rules": rules,
        "services": services,
    }


def render() -> str:
    return json.dumps(build_config(), ensure_ascii=False, indent=2) + "\n"


def main() -> None:
    sys.stdout.write(render())


if __name__ == "__main__":
    main()
