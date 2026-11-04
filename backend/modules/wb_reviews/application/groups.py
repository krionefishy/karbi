"""Группы болванок: склейки WB плюс ручные правки менеджера.

Болванка — копия основного товара, заведённая ради выдачи: без фото или с
одним фото, с номерным артикулом продавца. Чаще всего WB приклеивает её к
основной карточке (общий imtID), но бывают и неприклеенные, поэтому склейку
можно поправить руками: закрепить основной, привязать товар к основному,
вывести из группы.
"""

from dataclasses import dataclass
from typing import Literal

from backend.modules.wb_reviews.domain import GROUP_MAIN, GROUP_MEMBER, GROUP_SOLO, GroupOverride

GroupRole = Literal["main", "member"]
ROLE_MAIN: GroupRole = "main"
ROLE_MEMBER: GroupRole = "member"


@dataclass(frozen=True, slots=True)
class GroupCandidate:
    """Что нужно о товаре, чтобы найти ему группу и выбрать основной."""

    article: str
    imt_id: int | None
    photo_count: int | None
    reviews: int


@dataclass(frozen=True, slots=True)
class Placement:
    group_id: str | None
    role: GroupRole | None
    manual: bool


def card_key(article: str, imt_id: int | None) -> str:
    """Склейка WB; без imtID товар сам себе склейка."""
    return f"imt:{imt_id}" if imt_id is not None else f"nm:{article}"


def group_keys(candidates: list[GroupCandidate], overrides: dict[str, GroupOverride]) -> dict[str, str]:
    """Ключ эффективной группы каждого товара.

    Привязка (`main_article` у `member` и у закреплённого `main`) ведёт в группу
    цели; цепочка привязок проходится до конца. Если она замкнулась (A к B,
    потом B к A), корнем считается меньший артикул кольца — так все его
    участники попадают в одну группу.
    """
    known = {candidate.article: candidate for candidate in candidates}

    def own_key(article: str) -> str:
        override = overrides.get(article)
        if override is not None and override.kind == GROUP_SOLO:
            return f"nm:{article}"
        return card_key(article, known[article].imt_id)

    def target_of(article: str) -> str | None:
        override = overrides.get(article)
        if override is None or override.main_article is None or override.main_article not in known:
            return None
        return override.main_article

    def root_of(article: str) -> str:
        path: list[str] = []
        current: str | None = article
        while current is not None and current not in path:
            path.append(current)
            current = target_of(current)
        if current is None:
            return path[-1]
        return min(path[path.index(current) :])

    keys = {candidate.article: own_key(root_of(candidate.article)) for candidate in candidates}
    return keys


def _auto_rank(candidate: GroupCandidate) -> tuple[int, int, int, str]:
    numeric = int(candidate.article) if candidate.article.isdigit() else 0
    return (candidate.photo_count or 0, candidate.reviews, -numeric, candidate.article)


def resolve_groups(candidates: list[GroupCandidate], overrides: dict[str, GroupOverride]) -> dict[str, Placement]:
    """Группа и роль каждого товара. Группа из одного товара — не группа."""
    keys = group_keys(candidates, overrides)
    members: dict[str, list[GroupCandidate]] = {}
    for candidate in candidates:
        members.setdefault(keys[candidate.article], []).append(candidate)

    placements: dict[str, Placement] = {}
    for key, group in members.items():
        if len(group) == 1:
            article = group[0].article
            placements[article] = Placement(None, None, article in overrides)
            continue
        pinned = [
            overrides[candidate.article]
            for candidate in group
            if candidate.article in overrides and overrides[candidate.article].kind == GROUP_MAIN
        ]
        if pinned:
            main = max(pinned, key=lambda override: (override.updated_at, override.article)).article
        else:
            main = max(group, key=_auto_rank).article
        for candidate in group:
            override = overrides.get(candidate.article)
            if candidate.article == main:
                placements[candidate.article] = Placement(key, ROLE_MAIN, bool(pinned))
            else:
                manual = override is not None and override.kind in (GROUP_MEMBER, GROUP_MAIN)
                placements[candidate.article] = Placement(key, ROLE_MEMBER, manual)
    return placements
