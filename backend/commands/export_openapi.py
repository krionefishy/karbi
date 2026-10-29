"""Схема OpenAPI в JSON — для типов фронта и расширения.

`--tag wb-screenshot-control` оставляет маршруты с тегами, начинающимися с этого префикса, и только
нужные им схемы. Приложение собирается без подключения к базе и брокеру.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from backend.app.factory import create_app
from backend.shared.settings import load_settings

_REF_PREFIX = "#/components/schemas/"


def _refs(node: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith(_REF_PREFIX):
            found.add(ref[len(_REF_PREFIX) :])
        for value in node.values():
            found |= _refs(value)
    elif isinstance(node, list):
        for item in node:
            found |= _refs(item)
    return found


def filter_by_tag(spec: dict[str, Any], tag: str) -> dict[str, Any]:
    paths: dict[str, Any] = {}
    for path, operations in spec.get("paths", {}).items():
        kept = {
            method: operation
            for method, operation in operations.items()
            if any(name.startswith(tag) for name in operation.get("tags", []))
        }
        if kept:
            paths[path] = kept
    schemas = spec.get("components", {}).get("schemas", {})
    needed: set[str] = set()
    pending = _refs(paths)
    while pending:
        name = pending.pop()
        if name in needed or name not in schemas:
            continue
        needed.add(name)
        pending |= _refs(schemas[name])
    return {
        **{key: value for key, value in spec.items() if key not in ("paths", "components")},
        "paths": paths,
        "components": {"schemas": {name: schemas[name] for name in sorted(needed)}},
    }


def build_spec(tag: str | None = None) -> dict[str, Any]:
    spec = create_app(load_settings()).openapi()
    return filter_by_tag(spec, tag) if tag else spec


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the OpenAPI schema as JSON")
    parser.add_argument("--tag", default=None, help="keep only operations whose tag starts with this prefix")
    parser.add_argument("--out", default=None, help="file to write; stdout by default")
    args = parser.parse_args()
    text = json.dumps(build_spec(args.tag), ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
