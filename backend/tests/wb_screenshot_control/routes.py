from collections.abc import Iterable, Iterator
from typing import Any


def walk_routes(routes: Iterable[Any]) -> Iterator[Any]:
    """Маршруты с итоговым путём: подключённый роутер FastAPI отдаёт их через effective_route_contexts."""
    for route in routes:
        contexts = getattr(route, "effective_route_contexts", None)
        if contexts is not None:
            yield from contexts()
        elif hasattr(route, "dependant"):
            yield route
