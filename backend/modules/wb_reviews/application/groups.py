"""Группы болванок: основной товар и его болванки, плюс ручные правки менеджера.

Болванка — копия товара, заведённая ради выдачи. Основной товар отличает
остаток: у него FBO + FBS (с товаром в пути) больше нуля, у болванки — ноль.
Фото, артикул продавца и название у болванок бывают любыми, поэтому ключом
группы ни одно из них не служит, а только подсказывает, к какому товару
болванка относится:

1. товар её склейки WB; если их в склейке несколько — с тем же названием,
   затем с тем же артикулом продавца (без номера и «фото»);
2. товар с тем же названием, затем с тем же артикулом, затем товар предмета,
   чьё название начинается с названия болванки;
3. товар, к которому ушли приклеенные болванки с тем же названием.

Когда подсказки не различают товары, болванка идёт к товару с большим
остатком. Больше 30 болванок у товара не бывает: лишние, привязанные по самым
слабым подсказкам, остаются отдельными строками до ручной привязки.

Без сведений об остатках (зеркало ещё не собрало их) основным считается товар
от двух фото. Ручные правки главнее: закреплённый основной, привязка к другому
товару, вывод из группы.
"""

import re
from dataclasses import dataclass
from typing import Literal

from backend.modules.wb_reviews.domain import GROUP_MAIN, GROUP_MEMBER, GROUP_SOLO, GroupOverride

GroupRole = Literal["main", "member"]
ROLE_MAIN: GroupRole = "main"
ROLE_MEMBER: GroupRole = "member"
MAX_DUMMIES = 30
FALLBACK_MIN_PHOTOS = 2

# Насколько надёжна подсказка, по которой болванка нашла товар: при переполнении
# группы первыми отцепляются самые слабые.
BY_CARD = 5
BY_CARD_AND_HINT = 4
BY_NAME = 3
BY_VENDOR_CODE = 2
BY_NEIGHBOURS = 1
BY_STOCK_ONLY = 0


@dataclass(frozen=True, slots=True)
class GroupCandidate:
    """Что нужно о товаре, чтобы найти ему группу и выбрать основной.

    `stock` — остаток FBO + FBS с товаром в пути; `None` — остатков по селлеру нет.
    """

    article: str
    imt_id: int | None
    photo_count: int | None
    reviews: int
    name: str = ""
    stock: int | None = None
    vendor_code: str = ""
    subject: str = ""

    @property
    def is_main(self) -> bool:
        if self.stock is not None:
            return self.stock > 0
        return (self.photo_count or 0) >= FALLBACK_MIN_PHOTOS


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


def vendor_stem(vendor_code: str) -> str:
    """Начало артикула продавца без номера и пометки «фото»: «Шуруповерт  20» и «шуруповёрт3» → «шуруповерт»."""
    stem = re.sub(r"\s*фото$", "", name_key(vendor_code))
    return re.sub(r"\s*\d+$", "", stem).strip()


def _same_product(real_stem: str, dummy_stem: str) -> bool:
    """Тот же артикул или артикул болванки уточняет артикул товара: «болгарка электрическая ушм»."""
    return bool(real_stem) and (dummy_stem == real_stem or dummy_stem.startswith(real_stem + " "))


def _closest(reals: list[GroupCandidate], dummy_stem: str) -> list[GroupCandidate]:
    """Товары, чей артикул совпадает с болванкой; при нескольких — с самым длинным общим началом."""
    if not dummy_stem:
        return []
    matching = [real for real in reals if _same_product(vendor_stem(real.vendor_code), dummy_stem)]
    if not matching:
        return []
    longest = max(len(vendor_stem(real.vendor_code)) for real in matching)
    return [real for real in matching if len(vendor_stem(real.vendor_code)) == longest]


def _by_name_prefix(reals: list[GroupCandidate], dummy_name: str) -> GroupCandidate | None:
    """Единственный товар, чьё название начинается с названия болванки (не короче двух слов)."""
    name = name_key(dummy_name)
    if len(name.split()) < 2:
        return None
    matching = [real for real in reals if _same_product(name, name_key(real.name))]
    return matching[0] if len(matching) == 1 else None


def _strength(candidate: GroupCandidate) -> tuple[int, int, int, int]:
    numeric = int(candidate.article) if candidate.article.isdigit() else 0
    return (candidate.stock or 0, candidate.reviews, candidate.photo_count or 0, -numeric)


def _pick(options: list[GroupCandidate], dummy: GroupCandidate) -> tuple[GroupCandidate, bool]:
    """Товар из нескольких: по названию, затем по артикулу; `False` — подсказки не помогли, взят больший остаток."""
    if len(options) == 1:
        return options[0], True
    key = name_key(dummy.name)
    named = [real for real in options if key and name_key(real.name) == key]
    if len(named) == 1:
        return named[0], True
    pool = named or options
    by_code = _closest(pool, vendor_stem(dummy.vendor_code))
    if len(by_code) == 1:
        return by_code[0], True
    return max(by_code or pool, key=_strength), False


def auto_anchors(candidates: list[GroupCandidate]) -> dict[str, str]:
    """Основной товар, к которому болванка относится без ручных правок; основной — сам себе."""
    reals = [candidate for candidate in candidates if candidate.is_main]
    by_card: dict[int, list[GroupCandidate]] = {}
    by_name: dict[str, list[GroupCandidate]] = {}
    by_subject: dict[str, list[GroupCandidate]] = {}
    for real in reals:
        if real.imt_id is not None:
            by_card.setdefault(real.imt_id, []).append(real)
        if key := name_key(real.name):
            by_name.setdefault(key, []).append(real)
        by_subject.setdefault(real.subject, []).append(real)

    anchors = {candidate.article: candidate.article for candidate in candidates}
    found: dict[str, tuple[str, int]] = {}
    glued: dict[str, list[str]] = {}
    loose: list[GroupCandidate] = []
    for dummy in (candidate for candidate in candidates if not candidate.is_main):
        card = by_card.get(dummy.imt_id) if dummy.imt_id is not None else None
        if not card:
            loose.append(dummy)
            continue
        real, sure = _pick(card, dummy)
        found[dummy.article] = (
            real.article,
            BY_CARD if len(card) == 1 else BY_CARD_AND_HINT if sure else BY_STOCK_ONLY,
        )
        if key := name_key(dummy.name):
            glued.setdefault(key, []).append(real.article)

    for dummy in loose:
        key = name_key(dummy.name)
        same_subject = by_subject.get(dummy.subject, [])
        if key and key in by_name:
            real, sure = _pick(by_name[key], dummy)
            found[dummy.article] = (real.article, BY_NAME if sure else BY_STOCK_ONLY)
        elif by_code := _closest(same_subject, vendor_stem(dummy.vendor_code)):
            found[dummy.article] = (max(by_code, key=_strength).article, BY_VENDOR_CODE)
        elif (prefixed := _by_name_prefix(same_subject, dummy.name)) is not None:
            found[dummy.article] = (prefixed.article, BY_VENDOR_CODE)
        elif key in glued:
            neighbours = glued[key]
            found[dummy.article] = (max(sorted(set(neighbours)), key=neighbours.count), BY_NEIGHBOURS)

    members: dict[str, list[tuple[int, str]]] = {}
    for article, (main, evidence) in found.items():
        members.setdefault(main, []).append((evidence, article))
    for main, group in members.items():
        group.sort(key=lambda item: (-item[0], item[1]))
        for _, article in group[:MAX_DUMMIES]:
            anchors[article] = main
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
