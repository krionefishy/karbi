"""Типы фронта повторяют контракт вручную: словари union-литералов в `types.ts` сверяются со схемами.

Разбор — регуляркой по `export type Name = "a" | "b" ...;`; ссылки на другие типы внутри union
(`Status = "processing" | Verdict`) раскрываются по уже разобранным именам. Без БД.
"""

import re
from pathlib import Path
from typing import get_args

import pytest

from backend.modules.wb_screenshot_control.presentation.http import schemas

TYPES_TS = Path(__file__).resolve().parents[3] / "frontend/src/features/screenshotControl/types.ts"

CHECKED = (
    "Outcome",
    "Verdict",
    "Status",
    "Channel",
    "ReasonCode",
    "ManagerReasonCode",
    "ErrorCode",
    "MatchKind",
    "ResolveDecision",
    "QueueKind",
    "ManagerFlag",
)

_DECLARATION = re.compile(r"^export type (\w+)\s*=\s*([^;{]*);", re.MULTILINE)
_MEMBER = re.compile(r'^"([^"]*)"$')
_NAME = re.compile(r"^\w+$")


def _ts_unions(source: str) -> dict[str, list[str]]:
    raw = {name: body for name, body in _DECLARATION.findall(source)}
    resolved: dict[str, list[str]] = {}

    def resolve(name: str, seen: tuple[str, ...] = ()) -> list[str]:
        if name in resolved:
            return resolved[name]
        if name in seen or name not in raw:
            raise AssertionError(f"types.ts: не удалось разобрать тип {name}")
        values: list[str] = []
        for part in (piece.strip() for piece in raw[name].split("|")):
            if not part:
                continue
            literal = _MEMBER.match(part)
            if literal:
                values.append(literal.group(1))
            elif _NAME.match(part):
                values.extend(resolve(part, (*seen, name)))
            else:
                raise AssertionError(f"types.ts: в {name} не литерал и не имя типа: {part!r}")
        resolved[name] = values
        return values

    def is_union(body: str) -> bool:
        parts = [piece.strip() for piece in body.split("|") if piece.strip()]
        return bool(parts) and all(_MEMBER.match(part) or _NAME.match(part) for part in parts)

    return {name: resolve(name) for name, body in raw.items() if is_union(body)}


@pytest.fixture(scope="module")
def ts_unions() -> dict[str, list[str]]:
    return _ts_unions(TYPES_TS.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", CHECKED)
def test_frontend_union_matches_schema_literal(name: str, ts_unions: dict[str, list[str]]) -> None:
    assert name in ts_unions, f"в types.ts нет union-типа {name}"
    frontend = ts_unions[name]
    backend = list(get_args(getattr(schemas, name)))
    assert len(frontend) == len(set(frontend)), f"{name}: повтор значений в types.ts"
    missing = sorted(set(backend) - set(frontend))
    extra = sorted(set(frontend) - set(backend))
    assert not missing and not extra, f"{name}: нет во фронте {missing}, лишние во фронте {extra}"


def test_parser_expands_type_references() -> None:
    parsed = _ts_unions('export type A = "x" | "y";\nexport type B =\n  | "z"\n  | A;\n')
    assert parsed == {"A": ["x", "y"], "B": ["z", "x", "y"]}
