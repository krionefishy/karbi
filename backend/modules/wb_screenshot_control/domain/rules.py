"""Правила решения: словарь причин, сравнение кейса с кандидатом и проверки самого скриншота.

Порядок применения и выбор вердикта — в `decide.py`; здесь только «что нашли». Подпись-исход по
содержимому скриншота не проверяется, сверки с WB нет (решение заказчика): защита держится на
уникальности и эвристиках подлинности.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, tzinfo
from typing import Any, Literal, get_args
from zoneinfo import ZoneInfo

from backend.modules.wb_screenshot_control.domain.pipeline_types import (
    Candidate,
    DecisionInput,
    ExtractedFields,
    Match,
    Reason,
    Severity,
)
from backend.modules.wb_screenshot_control.domain.similarity import (
    CHAT_CONFUSED,
    PREFIX_MIN_FACTOR,
    TIME_CONFUSED,
    TIME_SAME_DAY,
    chat_similarity,
    client_messages,
    matched_messages,
    text_similarity,
    time_similarity,
)
from backend.modules.wb_screenshot_control.domain.timezones import messages_in_zone
from backend.shared.settings.config import ScreenshotControlConfig

ReasonCode = Literal[
    "exact_file",
    "case_key",
    "near_image",
    "near_image_conflict",
    "cross_user_duplicate",
    "caption_conflict",
    "same_chat_same_text_other_time",
    "same_chat_similar_text",
    "same_text_time_other_chat",
    "same_text_other_time",
    "messages_overlap",
    "possible_duplicate",
    "order_conflict",
    "weak_key",
    "extraction_disagreement_chat_id",
    "extraction_disagreement_time",
    "extraction_disagreement_text",
    "extraction_failed",
    "chat_id_unreadable",
    "review_time_unreadable",
    "review_date_implausible",
    "url_not_chat",
    "not_wb_screenshot",
    "unreadable",
    "cropped_missing_panel",
    "photo_of_screen",
    "possible_tampering",
    "late_upload",
    "clock_skew",
    "upload_burst",
    "no_outcome",
    "audit_sample",
]
"""Тот же закрытый словарь, что `ReasonCode` HTTP-контракта; расхождение ловит тест."""

REASON_CODES: tuple[str, ...] = get_args(ReasonCode)

Impact = Literal["rejected", "duplicate", "needs_review"]

SEVERITY: dict[str, Severity] = {
    "exact_file": "info",
    "case_key": "info",
    "near_image": "info",
    "near_image_conflict": "warn",
    "cross_user_duplicate": "high",
    "caption_conflict": "warn",
    "same_chat_same_text_other_time": "high",
    "same_chat_similar_text": "high",
    "same_text_time_other_chat": "high",
    "same_text_other_time": "high",
    "messages_overlap": "info",
    "possible_duplicate": "warn",
    "order_conflict": "warn",
    "weak_key": "info",
    "extraction_disagreement_chat_id": "warn",
    "extraction_disagreement_time": "warn",
    "extraction_disagreement_text": "warn",
    "extraction_failed": "warn",
    "chat_id_unreadable": "info",
    "review_time_unreadable": "info",
    "review_date_implausible": "warn",
    "url_not_chat": "warn",
    "not_wb_screenshot": "info",
    "unreadable": "info",
    "cropped_missing_panel": "info",
    "photo_of_screen": "warn",
    "possible_tampering": "high",
    "late_upload": "warn",
    "clock_skew": "warn",
    "upload_burst": "warn",
    "no_outcome": "info",
    "audit_sample": "info",
}

AGREEMENT_MIN = 0.8
"""Согласие VLM и OCR по chat_id и времени ниже этого — поле неуверенное (раздел 6.4)."""

VERDICT_HOLDERS = ("accepted", "duplicate", "needs_review", None)
"""Кандидаты, которые «держат» кейс: с ними похожий текст в том же чате — повод для проверки. Скрин на
проверке (и заявленный, но ещё не решённый) тоже держит: иначе правка DOM рядом с ним проходит, а после
одобрения засчитываются оба. Отклонённые в кандидаты не попадают вовсе."""


@dataclass(frozen=True, slots=True)
class Finding:
    """Найденная причина. `impact` — какой вердикт она даёт, если окажется первой жёсткой."""

    code: ReasonCode
    impact: Impact | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def reason(self) -> Reason:
        return Reason(code=self.code, severity=SEVERITY[self.code], detail=dict(self.detail))


@dataclass(frozen=True, slots=True)
class CaseHit:
    """Совпадение кейса с кандидатом: дубль или повод для проверки."""

    candidate: Candidate
    code: ReasonCode
    impact: Literal["duplicate", "needs_review"]
    match: Match


def _same(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and a == b


def _differs(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and a != b


def _hit(
    candidate: Candidate,
    code: ReasonCode,
    impact: Literal["duplicate", "needs_review"],
    kind: Literal["case_key", "chat_text", "text_time", "messages"],
    score: float,
    **details: Any,
) -> CaseHit:
    match = Match(
        other_submission_id=candidate.submission_id,
        kind=kind,
        score=round(score, 4),
        details={
            "code": code,
            **{key: round(value, 4) if isinstance(value, float) else value for key, value in details.items()},
        },
    )
    return CaseHit(candidate=candidate, code=code, impact=impact, match=match)


def key_text_len(fields: ExtractedFields) -> int:
    """Длина текстового ключа: у телефона — все видимые реплики покупателя, иначе текст отзыва."""
    if fields.case_kind == "mobile":
        messages = client_messages(fields.messages)
        if messages:
            return sum(len(message.text_norm) for message in messages)
    return len(fields.text_norm or "")


def key_dated(fields: ExtractedFields) -> bool:
    """Видна ли дата у времени ключа. У десктопа дата есть всегда; у телефона — если у последней
    реплики покупателя прочитаны день и месяц. Телефон без списка реплик считается датированным:
    дату тогда восстановила нормализация из `review_at`."""
    if fields.case_kind != "mobile":
        return True
    messages = client_messages(fields.messages)
    if not messages:
        return True
    last = messages[-1]
    return last.day is not None and last.month is not None


def has_time(fields: ExtractedFields) -> bool:
    return fields.review_at is not None or bool(client_messages(fields.messages))


def compare_case(x: ExtractedFields, candidate: Candidate, cfg: ScreenshotControlConfig, tz: tzinfo) -> CaseHit | None:
    """Ключ кейса (7.4–7.5 с правками ревью) против одного кандидата.

    `tz` — пояс загрузившего этот скрин. Моменты отзыва обоих уже в UTC и сравниваются в нём; дата
    «только день» берётся в поясе той стороны, у которой она такая. Время реплик — как на экране,
    поэтому реплики кандидата из другого пояса сначала переводятся в пояс этого скрина.
    """
    y = candidate.fields
    if y is None:
        return None
    x_zone, y_zone = x.timezone or cfg.timezone, y.timezone or cfg.timezone
    if x_zone != y_zone:
        y = replace(y, messages=messages_in_zone(y.messages, source=y_zone, target=x_zone, at=candidate.received_at))
    t = cfg.thresholds
    s_text = text_similarity(
        x.text_norm,
        y.text_norm,
        a_truncated=x.text_truncated,
        b_truncated=y.text_truncated,
        min_text_len=t.min_text_len,
    )
    s_time = time_similarity(
        x.review_at, x.review_time_precision, y.review_at, y.review_time_precision, tz, b_tz=ZoneInfo(y_zone)
    )
    if x.chat_id and y.chat_id:
        return _compare_with_chats(x, y, candidate, cfg, s_text, s_time)
    return _compare_without_chat(x, y, candidate, cfg, s_text, s_time)


def _compare_with_chats(
    x: ExtractedFields,
    y: ExtractedFields,
    candidate: Candidate,
    cfg: ScreenshotControlConfig,
    s_text: float,
    s_time: float,
) -> CaseHit | None:
    t = cfg.thresholds
    s_chat = chat_similarity(x.chat_id, x.chat_id_source, y.chat_id, y.chat_id_source)
    scores = {"s_chat": s_chat, "s_time": s_time, "s_text": s_text}
    if s_chat >= CHAT_CONFUSED:
        if s_time >= TIME_CONFUSED and s_text >= t.text_dup:
            return _hit(candidate, "case_key", "duplicate", "case_key", s_text, **scores)
        if s_text >= t.text_dup:
            return _hit(candidate, "same_chat_same_text_other_time", "needs_review", "chat_text", s_text, **scores)
        if s_time >= TIME_CONFUSED and s_text >= t.text_maybe:
            return _hit(candidate, "possible_duplicate", "needs_review", "case_key", s_text, **scores)
        if s_text >= t.text_maybe and candidate.verdict in VERDICT_HOLDERS:
            # Правка DOM: тот же чат, текст ±пара слов, другое время — заново кейс не засчитываем.
            return _hit(candidate, "same_chat_similar_text", "needs_review", "chat_text", s_text, **scores)
        # Тот же покупатель, другой отзыв (коллизия 1); общая история чата ключом здесь не служит.
        return None
    # Чаты разные: ключ без чата ловит подмену chatId (url вкладки от клиента ничем не проверен).
    if s_time >= TIME_CONFUSED and s_text >= t.text_dup:
        return _hit(candidate, "same_text_time_other_chat", "needs_review", "text_time", s_text, **scores)
    pairs = matched_messages(client_messages(x.messages), client_messages(y.messages), text_threshold=t.text_dup)
    if len(pairs) >= 2 and sum(pair.text_len for pair in pairs) >= t.min_text_len:
        return _hit(
            candidate, "same_text_time_other_chat", "needs_review", "messages", 1.0, s_chat=s_chat, messages=len(pairs)
        )
    return _same_text_any_time(x, y, candidate, cfg, s_text, scores)


def _same_text_any_time(
    x: ExtractedFields,
    y: ExtractedFields,
    candidate: Candidate,
    cfg: ScreenshotControlConfig,
    s_text: float,
    scores: dict[str, float],
) -> CaseHit | None:
    """Чат другой или неизвестен, а длинный текст тот же или почти тот же — при любом времени.

    Ловит подмену chatId вместе со второй правкой (сдвиг времени, замена слов): по отдельности их
    ловят ключи выше, вместе — только текст. Без чата и времени текст должен быть вдвое длиннее
    `min_text_len` (как у префиксного сравнения): шаблонные фразы вроде «не соответствует описанию»
    совпадают у разных покупателей и в разных чатах (коллизия 12 из 7.6).
    """
    t = cfg.thresholds
    text_len = min(len(x.text_norm or ""), len(y.text_norm or ""))
    if s_text >= t.text_maybe and text_len >= PREFIX_MIN_FACTOR * t.min_text_len:
        return _hit(candidate, "same_text_other_time", "needs_review", "text_time", s_text, **scores)
    return None


def _compare_without_chat(
    x: ExtractedFields,
    y: ExtractedFields,
    candidate: Candidate,
    cfg: ScreenshotControlConfig,
    s_text: float,
    s_time: float,
) -> CaseHit | None:
    """Телефон или десктоп без chatId: время + текст, набор реплик и слабые признаки."""
    t = cfg.thresholds
    if _differs(x.buyer_hmac, y.buyer_hmac) or _differs(x.article_norm, y.article_norm):
        return None  # слабые признаки противоречат — разные кейсы (7.5)
    confirmed = _same(x.buyer_hmac, y.buyer_hmac) or _same(x.article_norm, y.article_norm)
    dated = key_dated(x) and key_dated(y)
    text_len = min(len(x.text_norm or ""), len(y.text_norm or ""))
    scores = {"s_time": s_time, "s_text": s_text}
    key_match = s_time >= TIME_CONFUSED and s_text >= t.text_dup
    if key_match and text_len >= t.min_text_len and (dated or confirmed):
        return _hit(candidate, "case_key", "duplicate", "case_key", s_text, **scores)

    pairs = matched_messages(client_messages(x.messages), client_messages(y.messages), text_threshold=t.text_dup)
    matched_len = sum(pair.text_len for pair in pairs)
    overlap = {"messages": len(pairs), "matched_len": matched_len}
    if matched_len >= t.min_text_len and (len(pairs) >= 2 or any(pair.dated for pair in pairs) or confirmed):
        # Продолжение диалога меняет последнюю реплику, но набор пересекается — тот же кейс.
        return _hit(candidate, "messages_overlap", "duplicate", "messages", 1.0, **overlap)

    if key_match:
        if text_len < t.min_text_len:
            return _hit(candidate, "weak_key", "needs_review", "text_time", s_text, **scores, text_len=text_len)
        # Время без даты в окне 90 дней — слабый признак: только вместе с текстом и слабыми признаками.
        return _hit(candidate, "possible_duplicate", "needs_review", "text_time", s_text, **scores, dated=False)
    if pairs:
        if matched_len < t.min_text_len:
            return _hit(candidate, "weak_key", "needs_review", "messages", 1.0, **overlap)
        return _hit(candidate, "possible_duplicate", "needs_review", "messages", 1.0, **overlap, dated=False)

    if s_text >= t.text_dup and text_len >= t.min_text_len:
        if s_time == TIME_SAME_DAY:
            return _hit(candidate, "possible_duplicate", "needs_review", "text_time", s_text, **scores)
        return _hit(candidate, "same_text_other_time", "needs_review", "text_time", s_text, **scores)
    return _same_text_any_time(x, y, candidate, cfg, s_text, scores)


def keys_contradict(x: ExtractedFields, y: ExtractedFields | None, cfg: ScreenshotControlConfig, tz: tzinfo) -> bool:
    """Поля обоих прочитаны и расходятся: похожая картинка при разном содержимом подозрительна."""
    if y is None:
        return False
    t = cfg.thresholds
    if (
        x.chat_id
        and y.chat_id
        and chat_similarity(x.chat_id, x.chat_id_source, y.chat_id, y.chat_id_source) < CHAT_CONFUSED
    ):
        return True
    if x.text_norm and y.text_norm:
        s_text = text_similarity(
            x.text_norm,
            y.text_norm,
            a_truncated=x.text_truncated,
            b_truncated=y.text_truncated,
            min_text_len=t.min_text_len,
        )
        if s_text < t.text_maybe:
            return True
    if (
        x.review_at is not None
        and y.review_at is not None
        and x.review_time_precision == "minute"
        and y.review_time_precision == "minute"
    ):
        return time_similarity(x.review_at, "minute", y.review_at, "minute", tz) < TIME_CONFUSED
    return False


def keys_unread(fields: ExtractedFields | None) -> bool:
    """Ни chatId, ни текста, ни реплик — сравнивать кейс не по чему."""
    if fields is None:
        return True
    return not fields.chat_id and not fields.text_norm and not client_messages(fields.messages)


# --- проверки самого скриншота (строки 3–4 и 8–17 таблицы 8.2) ---


def content_rejections(fields: ExtractedFields) -> list[Finding]:
    """Строки 3–4: не скрин чата WB; фото экрана; нечитаемый или обрезанный, когда ключевые поля не прочитаны."""
    if fields.layout == "other" or not fields.is_wb_seller_chat:
        return [Finding("not_wb_screenshot", "rejected", {"layout": fields.layout})]
    if fields.layout == "unreadable":
        return [Finding("unreadable", "rejected", {"layout": fields.layout})]
    if fields.quality.photo_of_screen or fields.quality.moire_or_glare:
        return [Finding("photo_of_screen", "rejected")]
    if keys_unread(fields):
        if fields.quality.cropped_missing_panel:
            return [Finding("cropped_missing_panel", "rejected")]
        if fields.quality.blurred:
            return [Finding("unreadable", "rejected", {"blurred": True})]
    return []


def disagreement_findings(fields: ExtractedFields, cfg: ScreenshotControlConfig) -> list[Finding]:
    """Строка 9: VLM и OCR разошлись по полю ключа."""
    limits = {"chat_id": AGREEMENT_MIN, "time": AGREEMENT_MIN, "text": cfg.thresholds.vlm_ocr_text}
    fields_codes: tuple[tuple[str, ReasonCode], ...] = (
        ("chat_id", "extraction_disagreement_chat_id"),
        ("time", "extraction_disagreement_time"),
        ("text", "extraction_disagreement_text"),
    )
    findings = []
    for name, code in fields_codes:
        agreement = fields.agreement.get(name)
        low = agreement is not None and agreement < limits[name]
        if name in fields.disagreements or low:
            detail: dict[str, Any] = {} if agreement is None else {"agreement": round(agreement, 4)}
            findings.append(Finding(code, "needs_review", detail))
    return findings


def chat_findings(inp: DecisionInput, fields: ExtractedFields, cfg: ScreenshotControlConfig) -> list[Finding]:
    """Строка 10: у десктопа нет chatId; у расширения вкладка — не чат WB."""
    url = inp.hints.url
    if inp.channel == "extension" and url and not url.startswith(cfg.allowed_url_prefixes):
        return [Finding("url_not_chat", "needs_review")]
    if fields.case_kind == "desktop" and not fields.chat_id:
        return [Finding("chat_id_unreadable", "needs_review")]
    return []


def time_findings(
    inp: DecisionInput, fields: ExtractedFields, cfg: ScreenshotControlConfig, tz: tzinfo
) -> list[Finding]:
    """Строки 11–12: время отзыва не прочитано; дата старше `review_age_max_days` или в будущем."""
    if not has_time(fields):
        return [Finding("review_time_unreadable", "needs_review")]
    review_at = fields.review_at
    if review_at is None:
        return []
    received = inp.received_at
    if fields.review_time_precision == "day":
        future = review_at.astimezone(tz).date() > received.astimezone(tz).date()
    else:
        future = review_at > received + timedelta(minutes=cfg.clock_skew_minutes)
    too_old = received - review_at > timedelta(days=cfg.review_age_max_days)
    if future or too_old:
        return [Finding("review_date_implausible", "needs_review", {"review_at": _iso(review_at)})]
    return []


def weak_key_findings(fields: ExtractedFields, cfg: ScreenshotControlConfig) -> list[Finding]:
    """Строка 13: короткий текст без chatId или нет времени.

    У телефона ключ — все реплики покупателя вместе, а не одна последняя: короткое «Ужасно» при
    длинной истории ключ не ослабляет (иначе массовая ручная проверка, замечание ревью).
    """
    if not has_time(fields):
        return [Finding("weak_key", "needs_review", {"why": "no_time"})]
    if not fields.chat_id and key_text_len(fields) < cfg.thresholds.min_text_len:
        return [Finding("weak_key", "needs_review", {"why": "short_text", "text_len": key_text_len(fields)})]
    return []


def authenticity_findings(fields: ExtractedFields) -> list[Finding]:
    """Строка 15: признаки правки. Фото экрана отклоняется раньше, в `content_rejections`."""
    findings = []
    quality = fields.quality
    if quality.looks_edited:
        findings.append(Finding("possible_tampering", "needs_review", {"looks_edited": True}))
    return findings


def behaviour_findings(inp: DecisionInput, cfg: ScreenshotControlConfig) -> list[Finding]:
    """Строки 16–17: часы расширения, поздняя выгрузка из очереди, всплеск загрузок."""
    findings = []
    if inp.queued_at is not None:
        lag = inp.received_at - inp.queued_at
        if lag > timedelta(hours=cfg.late_upload_hours):
            findings.append(Finding("late_upload", "needs_review", {"lag_hours": round(lag.total_seconds() / 3600, 1)}))
    elif inp.channel == "extension" and inp.hints.captured_at is not None:
        skew = abs(inp.hints.captured_at - inp.received_at)
        if skew > timedelta(minutes=cfg.clock_skew_minutes):
            findings.append(
                Finding("clock_skew", "needs_review", {"skew_minutes": round(skew.total_seconds() / 60, 1)})
            )
    if inp.uploads_last_hour > cfg.burst_per_hour:
        findings.append(Finding("upload_burst", "needs_review", {"uploads_last_hour": inp.uploads_last_hour}))
    return findings


def _iso(value: datetime) -> str:
    return value.isoformat()
