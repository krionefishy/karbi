"""Решение по скриншоту: таблица 8.2 сверху вниз, первая жёсткая причина задаёт вердикт.

Чистая функция без БД и сети: кандидатов подбирает вызывающий (B1), здесь они только сравниваются.
Результат не зависит от порядка кандидатов и детерминирован; случайна только аудит-выборка,
и та по умолчанию сеется id сабмишена.
"""

import random
from collections.abc import Sequence
from typing import Literal
from zoneinfo import ZoneInfo

from backend.modules.wb_screenshot_control.domain.pipeline_types import (
    Candidate,
    Decision,
    DecisionInput,
    ExtractedFields,
    Match,
)
from backend.modules.wb_screenshot_control.domain.rules import (
    CaseHit,
    Finding,
    ReasonCode,
    authenticity_findings,
    behaviour_findings,
    chat_findings,
    compare_case,
    content_rejections,
    disagreement_findings,
    keys_contradict,
    keys_unread,
    time_findings,
    weak_key_findings,
)
from backend.modules.wb_screenshot_control.domain.similarity import hamming
from backend.shared.settings.config import ScreenshotControlConfig

Verdict = Literal["accepted", "duplicate", "needs_review", "rejected"]

_ROOT_VERDICTS = ("accepted", "needs_review", None)
"""Корнем цепочки становится сабмишен, который сам не дубль (отклонённые в кандидаты не попадают)."""


def decide(
    inp: DecisionInput,
    candidates: Sequence[Candidate],
    cfg: ScreenshotControlConfig,
    *,
    rng: random.Random | None = None,
) -> Decision:
    tz = ZoneInfo(cfg.timezone)
    pool = sorted(
        (c for c in candidates if c.submission_id != inp.submission_id and c.verdict != "rejected"),
        key=lambda c: (c.received_at, c.submission_id),
    )
    fields = inp.extraction.fields if inp.extraction.status == "ok" else None
    findings: list[Finding] = []
    matches: list[Match] = []

    # 1. Нет исхода к сроку.
    if inp.outcome is None:
        findings.append(Finding("no_outcome", "rejected"))

    # 2. Точная копия файла.
    exact = [c for c in pool if c.file_sha256 == inp.file_sha256]
    matches.extend(Match(c.submission_id, "exact_file", 1.0) for c in exact)

    hits = [hit for c in pool if fields is not None and (hit := compare_case(fields, c, cfg, tz)) is not None]
    matches.extend(hit.match for hit in hits)
    case_dups = [hit.candidate for hit in hits if hit.impact == "duplicate"]

    near_dups, near_conflicts, near_matches = _near_images(inp, fields, pool, hits, cfg, tz)
    matches.extend(near_matches)

    root = _root([*exact, *case_dups, *near_dups])
    duplicate_emitted = False
    if exact and root is not None:
        findings.extend(_duplicate_group("exact_file", root, inp))
        duplicate_emitted = True

    # 3–4. Не скрин чата WB, нечитаемый или обрезанный.
    if fields is not None:
        findings.extend(content_rejections(fields))

    # 5. Совпадение по ключу кейса.
    dup_hits = [hit for hit in hits if hit.impact == "duplicate"]
    if dup_hits and root is not None:
        lead = next((hit for hit in dup_hits if hit.candidate is root), dup_hits[0])
        if duplicate_emitted:
            findings.append(Finding(lead.code, None, _other(lead.candidate)))
        else:
            findings.extend(_duplicate_group(lead.code, root, inp))
            duplicate_emitted = True

    # 6. Ключей нет, картинка почти та же.
    if near_dups and root is not None and not duplicate_emitted:
        findings.extend(_duplicate_group("near_image", root, inp))
        duplicate_emitted = True

    # 7. Частичные совпадения и похожая картинка при противоречащих полях.
    for hit in hits:
        if hit.impact == "needs_review":
            findings.append(Finding(hit.code, "needs_review", _other(hit.candidate, hit.match.details)))
    for candidate, distance in near_conflicts:
        findings.append(
            Finding("near_image_conflict", "needs_review", {**_other(candidate), "dhash_distance": distance})
        )

    # 8. Распознавание не удалось.
    if fields is None:
        findings.append(Finding("extraction_failed", "needs_review", _extraction_error(inp)))
    else:
        findings.extend(_field_findings(inp, fields, cfg, tz))

    # 16–17. Поведение (не зависит от распознавания).
    findings.extend(behaviour_findings(inp, cfg))

    verdict: Verdict = next((f.impact for f in findings if f.impact is not None), "accepted")
    audit = False
    if verdict == "accepted":
        draw = (rng or random.Random(inp.submission_id.int)).random() * 100
        if draw < cfg.audit_percent:
            audit = True
            findings.append(Finding("audit_sample"))

    return Decision(
        verdict=verdict,
        reasons=tuple(f.reason() for f in _unique(findings)),
        duplicate_of=root.submission_id if verdict == "duplicate" and root is not None else None,
        matches=tuple(_unique_matches(matches)),
        audit_requested=audit,
    )


def _field_findings(
    inp: DecisionInput, fields: ExtractedFields, cfg: ScreenshotControlConfig, tz: ZoneInfo
) -> list[Finding]:
    """Строки 9–15 таблицы 8.2 по прочитанным полям."""
    return [
        *disagreement_findings(fields, cfg),
        *chat_findings(inp, fields, cfg),
        *time_findings(inp, fields, cfg, tz),
        *weak_key_findings(fields, cfg),
        *authenticity_findings(fields),
    ]


def _near_images(
    inp: DecisionInput,
    fields: ExtractedFields | None,
    pool: Sequence[Candidate],
    hits: Sequence[CaseHit],
    cfg: ScreenshotControlConfig,
    tz: ZoneInfo,
) -> tuple[list[Candidate], list[tuple[Candidate, int]], list[Match]]:
    """dHash всего кадра — только подсказка: дубль даёт лишь при непрочитанных полях.

    Мягкий порог пишется совпадением для админа; противоречие полей проверяется по строгому,
    потому что одинаковый интерфейс у разных отзывов даёт близкий хеш всего кадра.
    """
    t = cfg.thresholds
    hit_ids = {hit.candidate.submission_id for hit in hits}
    dups: list[Candidate] = []
    conflicts: list[tuple[Candidate, int]] = []
    matches: list[Match] = []
    for candidate in pool:
        if candidate.file_sha256 == inp.file_sha256:
            continue
        distance = hamming(inp.dhash, candidate.dhash)
        if distance is None or distance > t.near_dhash_soft:
            continue
        matches.append(Match(candidate.submission_id, "near_image", round(1 - distance / 64, 4), {"dhash": distance}))
        if distance > t.near_dhash_strict:
            continue
        if keys_unread(fields):
            dups.append(candidate)
        elif (
            fields is not None
            and candidate.submission_id not in hit_ids
            and keys_contradict(fields, candidate.fields, cfg, tz)
        ):
            conflicts.append((candidate, distance))
    return dups, conflicts, matches


def _root(matched: Sequence[Candidate]) -> Candidate | None:
    """Корень цепочки: самый ранний по `(received_at, id)` из тех, кто сам не дубль; если такие все
    дубли — самый ранний из них."""
    if not matched:
        return None
    return min(matched, key=lambda c: (c.verdict not in _ROOT_VERDICTS, c.received_at, c.submission_id))


def _duplicate_group(code: ReasonCode, root: Candidate, inp: DecisionInput) -> list[Finding]:
    """Дубль корня. Тот же менеджер с другой подписью — не дубль, а проверка: возможно, исправляет
    исход. Другой менеджер — дубль с высокой подозрительностью, подпись не важна."""
    other = _other(root)
    caption_differs = inp.outcome is not None and root.outcome is not None and inp.outcome != root.outcome
    if root.user_id == inp.user_id:
        if caption_differs:
            return [
                Finding(code, None, other),
                Finding(
                    "caption_conflict", "needs_review", {**other, "outcome": inp.outcome, "other_outcome": root.outcome}
                ),
            ]
        return [Finding(code, "duplicate", other)]
    group = [Finding(code, "duplicate", other), Finding("cross_user_duplicate", None, other)]
    if caption_differs:
        group.append(
            Finding("caption_conflict", None, {**other, "outcome": inp.outcome, "other_outcome": root.outcome})
        )
    return group


def _other(candidate: Candidate, details: dict[str, object] | None = None) -> dict[str, object]:
    extra = {key: value for key, value in (details or {}).items() if key != "code"}
    return {"other_submission_id": str(candidate.submission_id), **extra}


def _extraction_error(inp: DecisionInput) -> dict[str, object]:
    error = inp.extraction.error
    return {"error": error[:200]} if error else {}


def _unique(findings: Sequence[Finding]) -> list[Finding]:
    """Одна причина на код: первая по порядку таблицы (кандидаты уже упорядочены)."""
    seen: set[str] = set()
    result = []
    for finding in findings:
        if finding.code not in seen:
            seen.add(finding.code)
            result.append(finding)
    return result


def _unique_matches(matches: Sequence[Match]) -> list[Match]:
    seen: set[tuple[object, str]] = set()
    result = []
    for match in matches:
        key = (match.other_submission_id, match.kind)
        if key not in seen:
            seen.add(key)
            result.append(match)
    return result
