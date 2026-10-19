import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
MODULES = BACKEND / "modules"
WORKERS = BACKEND / "workers"
PLATFORM = {"wb_core", "notifications", "platform"}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return {name.split(".")[2] for name in names if name.startswith("backend.modules.") and name.count(".") >= 2}


def _violations(root: Path) -> list[str]:
    found: list[str] = []
    for package in sorted(path for path in root.iterdir() if path.is_dir() and not path.name.startswith("_")):
        allowed = PLATFORM | {package.name}
        for file in sorted(package.rglob("*.py")):
            for target in sorted(_imported_modules(file) - allowed):
                found.append(f"{file.relative_to(BACKEND.parent)} -> backend.modules.{target}")
    return found


def test_modules_do_not_import_other_automations() -> None:
    assert _violations(MODULES) == []


def test_workers_do_not_import_other_automations() -> None:
    assert _violations(WORKERS) == []
