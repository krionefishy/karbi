"""Всё, что импортирует код сервиса, ставится в образ без dev-зависимостей.

Тесты гоняются в окружении с dev-группой, и пакет, который приехал только
транзитивно через dev-зависимость, здесь импортируется, а в образе — нет.
Так 25.09.2026 стикеры коробов упали на проде с `No module named 'PIL'`:
Pillow приезжала через reportlab, а reportlab перенесли в dev.
"""

import ast
import importlib.metadata as metadata
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = [ROOT / "backend" / name for name in ("app", "modules", "shared", "workers")]


def _name(requirement: str) -> tuple[str, set[str]]:
    match = re.match(r"\s*([A-Za-z0-9_.-]+)(?:\[([^\]]+)\])?", requirement)
    assert match, requirement
    extras = {item.strip() for item in (match.group(2) or "").split(",") if item.strip()}
    return _norm(match.group(1)), extras


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _runtime_closure() -> set[str]:
    """Основные зависимости из pyproject со всем, что они тянут (с их extras)."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    queue = [_name(item) for item in project["dependencies"]]
    seen: set[str] = set()
    while queue:
        name, extras = queue.pop()
        if name in seen:
            continue
        try:
            requirements = metadata.requires(name) or []
        except metadata.PackageNotFoundError:
            # Не установлен — значит, под этот Python не нужен (маркер версии).
            continue
        seen.add(name)
        for requirement in requirements:
            spec, _, marker = requirement.partition(";")
            wanted = re.findall(r"extra\s*==\s*[\"']([^\"']+)[\"']", marker)
            if wanted and not set(wanted) & extras:
                continue
            queue.append(_name(spec))
    return seen


def _imported_top_levels() -> set[str]:
    names: set[str] = set()
    for root in RUNTIME:
        for file in root.rglob("*.py"):
            tree = ast.parse(file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    names.add(node.module.split(".")[0])
    return {name for name in names if name != "backend" and name not in sys.stdlib_module_names}


def test_runtime_imports_come_from_main_dependencies() -> None:
    providers = metadata.packages_distributions()
    closure = _runtime_closure()
    missing = {
        module: sorted(_norm(dist) for dist in providers.get(module, []))
        for module in sorted(_imported_top_levels())
        if not {_norm(dist) for dist in providers.get(module, [])} & closure
    }
    assert missing == {}, f"Импортируется в коде сервиса, но не ставится в образ: {missing}"
