"""Группы болванок: основной товар и его болванки, плюс ручные правки менеджера.

Болванка — копия товара, заведённая ради выдачи: без фото или с одним фото, с
номерным артикулом продавца («шуруповёрт7», «Болгарка УШМ фото»). Склейка WB
(общий imtID) группой не является: в одну склейку селлеры кладут и несколько
настоящих товаров, а часть болванок не приклеивают вовсе. Поэтому группа
строится вокруг настоящего товара (от двух фото), а болванка ищет его так:

1. настоящий товар своей склейки — с тем же артикулом продавца (без номера и
   «фото») или чей артикул болванка уточняет, иначе самый отзывчивый;
2. такой же по артикулу настоящий товар того же предмета;
3. единственный товар того же предмета, чьё название начинается с названия болванки;
4. приклеенная болванка того же предмета с тем же артикулом — её группа;
5. единственный настоящий товар предмета.

Не нашлось — болванка стоит отдельно, её можно привязать руками.
"""

import re
from dataclasses import dataclass
from typing import Literal

from backend.modules.wb_reviews.domain import GROUP_MAIN, GROUP_MEMBER, GROUP_SOLO, GroupOverride

GroupRole = Literal["main", "member"]
ROLE_MAIN: GroupRole = "main"
ROLE_MEMBER: GroupRole = "member"
DUMMY_MAX_PHOTOS = 1


@dataclass(frozen=True, slots=True)
class GroupCandidate:
    """Что нужно о товаре, чтобы найти ему группу и выбрать основной."""

    article: str
    imt_id: int | None
    photo_count: int | None
    reviews: int
    vendor_code: str = ""
    subject: str = ""
    name: str = ""

    @property
    def is_dummy(self) -> bool:
        """Неизвестное число фото — не болванка: прятать настоящий товар хуже, чем показать лишнюю строку."""
        return self.photo_count is not None and self.photo_count <= DUMMY_MAX_PHOTOS


@dataclass(frozen=True, slots=True)
class Placement:
    group_id: str | None
    role: GroupRole | None
    manual: bool


def card_key(article: str, imt_id: int | None) -> str:
    """Склейка WB; без imtID товар сам себе склейка."""
    return f"imt:{imt_id}" if imt_id is not None else f"nm:{article}"


def vendor_stem(vendor_code: str) -> str:
    """Начало артикула продавца без номера и пометки «фото»: «Шуруповерт  20» и «шуруповёрт3» → «шуруповерт»."""
    stem = re.sub(r"[\W_]+", " ", vendor_code.lower().replace("ё", "е")).strip()
    stem = re.sub(r"\s*фото$", "", stem)
    stem = re.sub(r"\s*\d+$", "", stem)
    return stem.strip()


def _same_product(real_stem: str, dummy_stem: str) -> bool:
    """Тот же артикул или артикул болванки уточняет артикул товара: «болгарка электрическая ушм»."""
    return bool(real_stem) and (dummy_stem == real_stem or dummy_stem.startswith(real_stem + " "))


def _closest(reals: list[GroupCandidate], dummy_stem: str) -> list[GroupCandidate]:
    """Товары, чей артикул совпадает с болванкой; при нескольких — с самым длинным общим началом."""
    matching = [real for real in reals if _same_product(vendor_stem(real.vendor_code), dummy_stem)]
    if not matching:
        return []
    longest = max(len(vendor_stem(real.vendor_code)) for real in matching)
    return [real for real in matching if len(vendor_stem(real.vendor_code)) == longest]


def _by_name(reals: list[GroupCandidate], dummy_name: str) -> GroupCandidate | None:
    """Единственный товар, чьё название начинается с названия болванки.

    «Автомобильный насос компрессор беспроводной» — начало названия ровно
    одного товара. Короткое общее название («пылесос») подходит нескольким и
    ничего не решает.
    """
    name = vendor_stem(dummy_name)
    if len(name.split()) < 2:
        return None
    matching = [real for real in reals if _same_product(name, vendor_stem(real.name))]
    return matching[0] if len(matching) == 1 else None


def _strength(candidate: GroupCandidate) -> tuple[int, int, int]:
    numeric = int(candidate.article) if candidate.article.isdigit() else 0
    return (candidate.reviews, candidate.photo_count or 0, -numeric)


def _best(candidates: list[GroupCandidate]) -> GroupCandidate:
    return max(candidates, key=_strength)


def auto_anchors(candidates: list[GroupCandidate]) -> dict[str, str]:
    """Основной товар, к которому болванка относится без ручных правок; настоящий товар — сам себе."""
    reals = [candidate for candidate in candidates if not candidate.is_dummy]
    reals_by_card: dict[int, list[GroupCandidate]] = {}
    reals_by_subject: dict[str, list[GroupCandidate]] = {}
    for real in reals:
        if real.imt_id is not None:
            reals_by_card.setdefault(real.imt_id, []).append(real)
        reals_by_subject.setdefault(real.subject, []).append(real)

    anchors = {real.article: real.article for real in reals}
    glued: dict[tuple[str, str], list[str]] = {}
    loose: list[GroupCandidate] = []
    for dummy in (candidate for candidate in candidates if candidate.is_dummy):
        card = reals_by_card.get(dummy.imt_id) if dummy.imt_id is not None else None
        if not card:
            loose.append(dummy)
            continue
        stem = vendor_stem(dummy.vendor_code)
        anchor = _best(_closest(card, stem) or card)
        anchors[dummy.article] = anchor.article
        glued.setdefault((dummy.subject, stem), []).append(anchor.article)

    for dummy in loose:
        stem = vendor_stem(dummy.vendor_code)
        same_subject = reals_by_subject.get(dummy.subject, [])
        by_stem = _closest(same_subject, stem) if stem else []
        if by_stem:
            anchors[dummy.article] = _best(by_stem).article
        elif (named := _by_name(same_subject, dummy.name)) is not None:
            anchors[dummy.article] = named.article
        elif stem and (dummy.subject, stem) in glued:
            neighbours = glued[(dummy.subject, stem)]
            anchors[dummy.article] = max(set(neighbours), key=neighbours.count)
        elif len(same_subject) == 1:
            anchors[dummy.article] = same_subject[0].article
        else:
            anchors[dummy.article] = dummy.article
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
