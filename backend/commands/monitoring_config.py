"""Конфиг внешнего мониторинга (deploy/monitoring/application.json) из маршрутов API.

Мониторинг читает лог хостового nginx и сам ничего не знает о приложении:
какой путь к какому разделу относится и где в пути идентификатор, ему говорит
этот файл. Писать его руками — значит забыть про него при первом же новом
маршруте, поэтому он собирается из самого приложения, а тест сверяет файл с кодом.

    uv run python -m backend.commands.monitoring_config          # переписать файл
    uv run python -m backend.commands.monitoring_config --check  # только сверить
"""

import json
import re
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from backend.app.application import create_app

CONFIG_FILE = Path(__file__).resolve().parents[2] / "deploy/monitoring/application.json"
BASE_PATH = "/api/v1"
LOG_PATH = "/var/log/nginx/marketplace-auto-monitoring.json"

# Чем заменяется параметр пути в регулярном выражении. Узкий шаблон важен для
# uuid и чисел: `groups/order` не должен попасть в `groups/{group_id}`.
PARAM_PATTERNS = {
    "uuid": "[0-9a-fA-F-]{36}",
    "integer": "[0-9]+",
}
ANY_SEGMENT = "[^/]+"

# Раздел — это префикс пути после /api/v1. Первый сегмент у нас почти всегда
# `wb`, группировка мониторинга по нему бесполезна, поэтому делим на сервисы.
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

    # Сначала маршруты с меньшим числом «любых» сегментов: точное совпадение
    # должно выигрывать у строкового параметра на том же месте.
    ordered = sorted(
        routed.items(),
        key=lambda item: (sum(kind == "string" for kind in item[1].values()), item[0]),
    )
    rules = [_rule(path, params) for path, params in ordered]
    # Всё, чего в приложении нет, — сканеры и опечатки. Без этого правила каждый
    # такой путь стал бы отдельной строкой в отчётах и съел лимит max_routes.
    rules.append({"pattern": ".*", "route": "/[unknown]"})

    prefixes = sorted({_service_prefix(path) for path in routed})
    missing = [prefix for prefix in prefixes if prefix not in SERVICE_NAMES]
    if missing:
        raise SystemExit(f"нет названия раздела в SERVICE_NAMES: {', '.join(missing)}")

    services: list[dict[str, Any]] = [
        {
            "id": "api",
            "name": "API",
            "prefix": "/",
            # Имена контейнеров, а не сервисов compose: в сети мониторинга есть
            # свой `api` на том же порту, и короткое имя резолвилось бы в оба.
            "health_url": f"http://karbi-api-1:8000{BASE_PATH}/health/ready",
            "health_body": {"database": True, "redis": True},
        },
        {
            # Запросы фронта лежат вне /api/v1 и в отчёты не попадают; раздел
            # нужен только ради проверки доступности, префикс ни с чем не совпадает.
            "id": "frontend",
            "name": "Фронтенд",
            "prefix": "/-/frontend",
            "health_url": "http://karbi-frontend-1/health",
        },
    ]
    services += [
        {"id": prefix.strip("/").replace("/", "-"), "name": SERVICE_NAMES[prefix], "prefix": prefix}
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
    rendered = render()
    if "--check" in sys.argv[1:]:
        if not CONFIG_FILE.exists() or CONFIG_FILE.read_text() != rendered:
            raise SystemExit(
                f"{CONFIG_FILE.name} отстал от маршрутов API: uv run python -m backend.commands.monitoring_config"
            )
        return
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(rendered)


if __name__ == "__main__":
    main()
