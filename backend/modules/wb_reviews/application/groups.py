"""Группы болванок: основной товар и его болванки, плюс ручные правки менеджера.

Болванка — копия товара, заведённая ради выдачи. Селлер даёт ей то же название,
что у товара, а фото, артикул продавца и склейка WB у болванок бывают любыми:
в одной склейке лежат разные товары, часть болванок не приклеена вовсе, у
некоторых болванок полный набор фото. Поэтому группа — это карточки с одним
названием (без учёта регистра, «ё» и знаков), а основной в ней — товар с
наибольшим остатком, при равенстве — с большим числом отзывов и фото.

Ручные правки главнее: закреплённый основной, привязка к другому товару,
вывод из группы.
"""

import re
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
    name: str = ""
    stock: int = 0


@dataclass(frozen=True, slots=True)
class Placement:
    group_id: str | None
    role: GroupRole | None
    manual: bool


def card_key(article: str, imt_id: int | None) -> str:
    """Склейка WB; без imtID товар сам себе склейка."""
    return f"imt:{imt_id}" if imt_id is not None else f"nm:{article}"


def name_key(name: str) -> str:
    """Название без регистра, «ё» и знаков: «Шуруповёрт  аккумуляторный,» → «шуруповерт аккумуляторный»."""
    return re.sub(r"[\W_]+", " ", name.lower().replace("ё", "е")).strip()


def _strength(candidate: GroupCandidate) -> tuple[int, int, int, int]:
    numeric = int(candidate.article) if candidate.article.isdigit() else 0
    return (candidate.stock, candidate.reviews, candidate.photo_count or 0, -numeric)


def auto_anchors(candidates: list[GroupCandidate]) -> dict[str, str]:
    """Основной товар группы с тем же названием; товар без названия стоит отдельно."""
    by_name: dict[str, list[GroupCandidate]] = {}
    for candidate in candidates:
        key = name_key(candidate.name)
        if key:
            by_name.setdefault(key, []).append(candidate)
    anchors = {candidate.article: candidate.article for candidate in candidates}
    for group in by_name.values():
        main = max(group, key=_strength).article
        for candidate in group:
            anchors[candidate.article] = main
    return anchors


def group_roots(candidates: list[GroupCandidate], overrides: dict[str, GroupOverride]) -> dict[str, str]:
    """Корень группы каждого товара: тот, к кому ведёт цепочка привязок.

    Ручная привязка (`main_article` у `member` и у закреплённого `main`) главнее
    автоматической, `solo` её обрывает. Если цепочка замкнулась (A к B, потом
    B к A), корнем считается меньший артикул кольца — так все его участники
    попадают в одну группу.
    """
    known = {candidate.article for candidate in candidates}
    anchors = auto_anchors(candidates)

    def target_of(article: str) -> str | None:
        override = overrides.get(article)
        if override is not None:
            if override.kind == GROUP_SOLO:
                return None
            if override.main_article is not None and override.main_article in known:
                return override.main_article
        anchor = anchors.get(article, article)
        return anchor if anchor != article else None

    def root_of(article: str) -> str:
        path: list[str] = []
        current: str | None = article
        while current is not None and current not in path:
            path.append(current)
            current = target_of(current)
        if current is None:
            return path[-1]
        return min(path[path.index(current) :])

    return {candidate.article: root_of(candidate.article) for candidate in candidates}


def group_keys(candidates: list[GroupCandidate], overrides: dict[str, GroupOverride]) -> dict[str, str]:
    """Ключ эффективной группы каждого товара."""
    return {article: f"g:{root}" for article, root in group_roots(candidates, overrides).items()}


def resolve_groups(candidates: list[GroupCandidate], overrides: dict[str, GroupOverride]) -> dict[str, Placement]:
    """Группа и роль каждого товара. Группа из одного товара — не группа."""
    roots = group_roots(candidates, overrides)
    members: dict[str, list[GroupCandidate]] = {}
    for candidate in candidates:
        members.setdefault(roots[candidate.article], []).append(candidate)

    placements: dict[str, Placement] = {}
    for root, group in members.items():
        if len(group) == 1:
            article = group[0].article
            placements[article] = Placement(None, None, article in overrides)
            continue
        pinned = [
            overrides[candidate.article]
            for candidate in group
            if candidate.article in overrides and overrides[candidate.article].kind == GROUP_MAIN
        ]
        main = max(pinned, key=lambda override: (override.updated_at, override.article)).article if pinned else root
        key = f"g:{root}"
        for candidate in group:
            override = overrides.get(candidate.article)
            if candidate.article == main:
                placements[candidate.article] = Placement(key, ROLE_MAIN, bool(pinned))
            else:
                manual = override is not None and override.kind in (GROUP_MEMBER, GROUP_MAIN)
                placements[candidate.article] = Placement(key, ROLE_MEMBER, manual)
    return placements
