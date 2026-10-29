"""Админ-панель: очереди, карточка с совпадениями, решения по таблице переходов, пересчёт,
досрочное удаление файла, состояние конвейера и привязки Telegram.

Панель сама ничего не меняет: каждое решение админа — строка в `verdict_events` и событие
`screenshot.verdict-ready` с actor=admin. Имя покупателя не хранится и не отдаётся.
"""

import asyncio
import json
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.platform.infrastructure.postgres import UserRepository
from backend.modules.wb_screenshot_control.application.errors import (
    ServiceError,
    decode_cursor,
    encode_cursor,
    not_found,
)
from backend.modules.wb_screenshot_control.application.events import ScreenshotSubmitted, ScreenshotVerdictReady
from backend.modules.wb_screenshot_control.application.images import ImageLinks
from backend.modules.wb_screenshot_control.application.outbox import publish
from backend.modules.wb_screenshot_control.application.storage import delete_event
from backend.modules.wb_screenshot_control.application.submissions_service import reset_verdict
from backend.modules.wb_screenshot_control.domain import LAYOUTS, STAGE_DECIDED, STAGE_PROCESSING, STORAGE_STATES
from backend.modules.wb_screenshot_control.domain.ports import Staging
from backend.modules.wb_screenshot_control.domain.rules import REASON_CODES
from backend.modules.wb_screenshot_control.infrastructure.postgres.models import (
    ExtractionModel,
    StorageObjectModel,
    SubmissionModel,
    VerdictEventModel,
)
from backend.modules.wb_screenshot_control.infrastructure.postgres.queries import (
    ScreenshotQueries,
    TelegramLinks,
    Window,
)
from backend.modules.wb_screenshot_control.infrastructure.postgres.repository import ScreenshotRepository
from backend.shared.settings.config import ScreenshotControlConfig

Clock = Callable[[], datetime]
MATCH_KINDS = ("exact_file", "near_image", "case_key", "chat_text", "text_time", "messages")
SEVERITIES = ("info", "warn", "high")
DETAIL_LIMIT = 500


@dataclass(frozen=True, slots=True)
class Transition:
    to: str
    note_required: bool = False


# Таблица 8.3: (вердикт сейчас, решение) → вердикт после. Чего здесь нет — 409 transition_not_allowed.
TRANSITIONS: dict[tuple[str, str], Transition] = {
    ("needs_review", "approve"): Transition("accepted"),
    ("needs_review", "decline"): Transition("rejected", note_required=True),
    ("needs_review", "duplicate_of"): Transition("duplicate"),
    ("accepted", "decline"): Transition("rejected", note_required=True),
    ("accepted", "duplicate_of"): Transition("duplicate", note_required=True),
    ("accepted", "audit_ok"): Transition("accepted"),
    ("duplicate", "not_duplicate"): Transition("accepted"),
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def suspicion(submission: SubmissionModel) -> str | None:
    """high — есть причина с серьёзностью high (чужой дубль, тот же чат и текст, правка и т. п.);
    medium — прочие на проверке и дубли своего; low — аудит. Та же логика, что `SUSPICION_RANK`."""
    if any(isinstance(item, dict) and item.get("severity") == "high" for item in submission.reasons or []):
        return "high"
    if submission.verdict in ("needs_review", "duplicate"):
        return "medium"
    if submission.audit_requested and submission.audit_done_at is None:
        return "low"
    return None


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _share(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return min(max(number, 0.0), 1.0)


def reason_out(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict) or item.get("code") not in REASON_CODES:
        return None
    detail = dict(item.get("detail") or {})
    other = _uuid(detail.pop("other_submission_id", None))
    severity = item.get("severity")
    return {
        "code": item["code"],
        "severity": severity if severity in SEVERITIES else "info",
        "detail": json.dumps(detail, ensure_ascii=False, default=str)[:DETAIL_LIMIT] if detail else None,
        "other_submission_id": other,
    }


def reasons_out(items: Sequence[Any]) -> list[dict[str, Any]]:
    return [found for item in items or [] if (found := reason_out(item)) is not None]


def _message_time(message: dict[str, Any], received_at: datetime, tz: ZoneInfo) -> tuple[datetime | None, str | None]:
    """Время сообщения как на экране; год — по дате приёма (скрин не из будущего)."""
    day, month, hour, minute = (message.get(name) for name in ("day", "month", "hour", "minute"))
    if not isinstance(day, int) or not isinstance(month, int):
        return None, None
    local = received_at.astimezone(tz)
    precise = isinstance(hour, int) and isinstance(minute, int)
    try:
        moment = datetime(
            local.year,
            month,
            day,
            hour if isinstance(hour, int) and precise else 0,
            minute if isinstance(minute, int) and precise else 0,
            tzinfo=tz,
        )
        if moment > local + timedelta(days=1):
            moment = moment.replace(year=local.year - 1)
    except ValueError:
        return None, None
    return moment.astimezone(UTC), "minute" if precise else "day"


class AdminService:
    def __init__(
        self,
        session: AsyncSession,
        config: ScreenshotControlConfig,
        images: ImageLinks,
        staging: Staging,
        *,
        now: Clock = _utcnow,
    ) -> None:
        self.session = session
        self.config = config
        self.images = images
        self.staging = staging
        self.queries = ScreenshotQueries(session)
        self.repository = ScreenshotRepository(session)
        self.now = now
        self.tz = ZoneInfo(config.timezone)

    async def usernames(self) -> dict[uuid.UUID, str]:
        return {user.id: user.username for user in await UserRepository(self.session).list_all()}

    # --- очередь ---

    async def queue(
        self,
        kind: str,
        *,
        window: Window,
        user_id: uuid.UUID | None,
        reason: str | None,
        cursor: str | None,
        limit: int,
    ) -> dict[str, Any]:
        after: tuple[Any, ...] | None = None
        if cursor:
            values = decode_cursor(cursor, 2 if kind == "all" else 3)
            try:
                stamp, last_id = datetime.fromisoformat(values[-2]), uuid.UUID(values[-1])
                after = (stamp, last_id) if kind == "all" else (int(values[0]), stamp, last_id)
            except ValueError as error:
                raise ServiceError("bad_cursor", "Курсор страницы не подходит, начните список заново") from error
        rows = await self.queries.queue(
            kind, window=window, user_id=user_id, reason=reason, after=after, limit=limit + 1
        )
        more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = None
        if more:
            last, rank = rows[-1]
            keys = (last.received_at.isoformat(), last.id)
            next_cursor = encode_cursor(*keys) if kind == "all" else encode_cursor(rank, *keys)
        return {
            "items": await self.items([row for row, _ in rows]),
            "next_cursor": next_cursor,
            "counts": await self.queries.queue_counts(window=window, user_id=user_id),
        }

    async def items(self, submissions: Sequence[SubmissionModel]) -> list[dict[str, Any]]:
        others = await self.queries.submissions(
            other
            for item in submissions
            for match in item.matches or []
            if isinstance(match, dict) and (other := _uuid(match.get("other_submission_id"))) is not None
        )
        file_ids = {item.file_id for item in submissions} | {item.file_id for item in others.values()}
        extractions = await self.queries.extractions(file_ids, self.config.pipeline_version)
        objects = await self.queries.objects(file_ids)
        users = await self.usernames()
        return [self._item(item, extractions.get(item.file_id), objects, others, users) for item in submissions]

    def _item(
        self,
        submission: SubmissionModel,
        extraction: ExtractionModel | None,
        objects: dict[tuple[uuid.UUID, str], StorageObjectModel],
        others: dict[uuid.UUID, SubmissionModel],
        users: dict[uuid.UUID, str],
    ) -> dict[str, Any]:
        return {
            "id": submission.id,
            "user": {"id": submission.user_id, "username": users.get(submission.user_id, "")},
            "channel": submission.channel,
            "received_at": submission.received_at,
            "outcome": submission.outcome,
            "outcome_source": submission.outcome_source,
            "caption_raw": submission.caption_raw,
            "stage": submission.stage,
            "verdict": submission.verdict,
            "auto_verdict": submission.auto_verdict,
            "reasons": reasons_out(submission.reasons),
            "suspicion": suspicion(submission),
            "duplicate_of": submission.duplicate_of,
            "audit_requested": submission.audit_requested,
            "matches": self._matches(submission, objects, others, users),
            "fields": self._fields(submission, extraction),
            "agreement": self._agreement(extraction),
            "thumb_url": self.images.link(objects.get((submission.file_id, "thumb"))),
        }

    def _matches(
        self,
        submission: SubmissionModel,
        objects: dict[tuple[uuid.UUID, str], StorageObjectModel],
        others: dict[uuid.UUID, SubmissionModel],
        users: dict[uuid.UUID, str],
    ) -> list[dict[str, Any]]:
        found = []
        for match in submission.matches or []:
            if not isinstance(match, dict) or match.get("kind") not in MATCH_KINDS:
                continue
            other = others.get(_uuid(match.get("other_submission_id")))  # type: ignore[arg-type]
            if other is None:
                continue
            found.append(
                {
                    "other_id": other.id,
                    "other_user": {"id": other.user_id, "username": users.get(other.user_id, "")},
                    "other_received_at": other.received_at,
                    "other_outcome": other.outcome,
                    "other_verdict": other.verdict,
                    "kind": match["kind"],
                    "score": _share(match.get("score")) or 0.0,
                    "thumb_url": self.images.link(objects.get((other.file_id, "thumb"))),
                }
            )
        return found

    def _fields(self, submission: SubmissionModel, extraction: ExtractionModel | None) -> dict[str, Any] | None:
        if extraction is None or extraction.status != "ok" or not extraction.fields:
            return None
        fields = extraction.fields
        vlm = extraction.vlm_json or {}
        review: dict[str, Any] = vlm["review"] if isinstance(vlm.get("review"), dict) else {}
        chat: dict[str, Any] = vlm["chat_id"] if isinstance(vlm.get("chat_id"), dict) else {}
        stars = fields.get("stars")
        review_at = fields.get("review_at")
        messages = []
        for message in fields.get("messages") or []:
            if not isinstance(message, dict) or message.get("side") == "seller" or not message.get("text_norm"):
                continue
            at, precision = _message_time(message, submission.received_at, self.tz)
            messages.append({"text": message["text_norm"], "at": at, "time_precision": precision})
        quality = fields.get("quality") or {}
        return {
            "layout": fields.get("layout") if fields.get("layout") in LAYOUTS else None,
            "case_kind": submission.case_kind,
            "chat_id": fields.get("chat_id"),
            "chat_id_source": fields.get("chat_id_source"),
            "key_time": submission.key_time,
            "key_time_precision": submission.key_time_precision,
            "review_at": datetime.fromisoformat(review_at) if review_at else None,
            "review_text": fields.get("review_text"),
            "review_text_truncated": bool(fields.get("text_truncated")),
            "stars": stars if isinstance(stars, int) and 1 <= stars <= 5 else None,
            "article": fields.get("article_norm"),
            "client_messages": messages,
            "buyer_name_seen": bool(fields.get("buyer_hmac")),
            "quality": {name: bool(quality.get(name)) for name in _QUALITY},
            "confidence": {
                "chat_id": _share(chat.get("confidence")),
                "time": _share(review.get("date_confidence")),
                "text": _share(review.get("text_confidence")),
            },
        }

    @staticmethod
    def _agreement(extraction: ExtractionModel | None) -> dict[str, Any] | None:
        if extraction is None or not extraction.agreement:
            return None
        return {name: _share(extraction.agreement.get(name)) for name in ("chat_id", "time", "text")}

    # --- карточка ---

    async def detail(self, submission_id: uuid.UUID) -> dict[str, Any]:
        submission = await self.queries.submission(submission_id)
        if submission is None:
            raise not_found()
        item = (await self.items([submission]))[0]
        file = await self.queries.file(submission.file_id)
        assert file is not None
        extraction = (await self.queries.extractions([file.id], self.config.pipeline_version)).get(file.id)
        users = await self.usernames()
        meta = file.meta or {}
        client = submission.client_meta or {}
        return {
            **item,
            "client_url": submission.client_url,
            "client_captured_at": submission.client_captured_at,
            "client_tz_offset_min": submission.client_tz_offset_min,
            "client_meta": {
                "ext_version": client.get("ext_version"),
                "viewport_w": client.get("viewport_w"),
                "viewport_h": client.get("viewport_h"),
                "dpr": client.get("dpr"),
                "user_agent": client.get("user_agent"),
                "telegram_compressed": bool(client.get("telegram_compressed")),
            },
            "install_id": submission.install_id,
            "file": {
                "id": file.id,
                "sha256": file.sha256,
                "mime": file.mime,
                "size_bytes": file.size_bytes,
                "width": file.width,
                "height": file.height,
                "meta": {
                    "has_exif": bool(meta.get("has_exif")),
                    "exif_software": meta.get("exif_software"),
                    "has_alpha": bool(meta.get("has_alpha")),
                },
                "objects": [_object_out(stored) for stored in await self.queries.file_objects(file.id)],
                "submissions_count": await self.queries.file_submissions(file.id),
            },
            "extraction": _extraction_out(extraction, submission.attempts),
            "events": [_event_out(event, users) for event in await self.queries.events(submission.id)],
            "decided_at": submission.decided_at,
            "reviewed_by": _user_ref(submission.reviewed_by, users),
            "reviewed_at": submission.reviewed_at,
            "review_note": submission.review_note,
        }

    async def image_url(self, submission_id: uuid.UUID, kind: str) -> dict[str, Any]:
        """Оригинал — вложением со своим типом, миниатюра — inline."""
        submission = await self.queries.submission(submission_id)
        if submission is None:
            raise not_found()
        storage_kind = "orig" if kind == "original" else "thumb"
        stored = (await self.queries.objects([submission.file_id])).get((submission.file_id, storage_kind))
        return self.images.answer(stored, attachment=storage_kind == "orig", now=self.now())

    # --- решения ---

    async def resolve(
        self,
        submission_id: uuid.UUID,
        admin_id: uuid.UUID,
        *,
        decision: str,
        duplicate_of: uuid.UUID | None,
        outcome: str | None,
        note: str,
    ) -> dict[str, Any]:
        now = self.now()
        submission = await self.queries.submission(submission_id, for_update=True)
        if submission is None:
            raise not_found()
        transition = TRANSITIONS.get((submission.verdict or "", decision))
        audit_open = submission.audit_requested and submission.audit_done_at is None
        if transition is None or (decision == "audit_ok" and not audit_open) or submission.stage != STAGE_DECIDED:
            raise ServiceError("transition_not_allowed", "Такое решение для этого скриншота недоступно")
        note = note.strip()
        if transition.note_required and not note:
            raise ServiceError("note_required", "Укажите причину: без неё снять или отклонить скриншот нельзя")
        root = await self._duplicate_root(submission, duplicate_of) if decision == "duplicate_of" else None
        if transition.to == "accepted" and decision != "audit_ok":
            conflict = await self.queries.accepted_with_same_key(submission)
            if conflict is not None:
                raise ServiceError(
                    "exact_key_conflict",
                    "Тот же кейс (чат, время и текст отзыва) уже засчитан другим скриншотом: только «дубль»",
                )

        previous = submission.verdict
        submission.verdict = transition.to
        submission.duplicate_of = root
        if outcome is not None:
            submission.outcome = outcome
            submission.outcome_source = "admin"
            submission.outcome_set_at = now
        if audit_open and previous == "accepted":
            submission.audit_done_at = now
        submission.reviewed_by = admin_id
        submission.reviewed_at = now
        submission.review_note = note or None
        submission.updated_at = now
        self.repository.add_verdict_event(
            submission.id,
            actor=f"admin:{admin_id}",
            from_verdict=previous,
            to_verdict=transition.to,
            reasons=[],
            note=f"{decision}: {note}"[:1000] if note else decision,
        )
        if previous != transition.to:
            publish(
                self.session,
                ScreenshotVerdictReady(
                    submission_id=submission.id,
                    user_id=submission.user_id,
                    channel=submission.channel,  # type: ignore[arg-type]
                    verdict=transition.to,  # type: ignore[arg-type]
                    previous_verdict=previous,  # type: ignore[arg-type]
                    actor="admin",
                    decided_at=now,
                ),
            )
        await self.session.commit()
        return await self.detail(submission.id)

    async def _duplicate_root(self, submission: SubmissionModel, target_id: uuid.UUID | None) -> uuid.UUID:
        target = await self.queries.submission(target_id) if target_id is not None else None
        if target is None or submission.id in (target.id, target.duplicate_of):
            raise ServiceError("duplicate_target_invalid", "Оригинал для дубля не найден")
        return target.duplicate_of or target.id

    async def reprocess(self, submission_id: uuid.UUID, admin_id: uuid.UUID) -> dict[str, Any]:
        """Заново через распознавание и решение; прежний вердикт уходит в журнал."""
        now = self.now()
        submission = await self.queries.submission(submission_id, for_update=True)
        if submission is None:
            raise not_found()
        file = await self.queries.file(submission.file_id)
        assert file is not None
        if submission.verdict is not None:
            self.repository.add_verdict_event(
                submission.id,
                actor=f"admin:{admin_id}",
                from_verdict=submission.verdict,
                to_verdict=None,
                reasons=list(submission.reasons or []),
                note="пересчёт",
            )
        reset_verdict(submission)
        submission.stage = STAGE_PROCESSING
        submission.updated_at = now
        publish(
            self.session,
            ScreenshotSubmitted(
                submission_id=submission.id,
                file_id=submission.file_id,
                sha256=file.sha256,
                channel=submission.channel,  # type: ignore[arg-type]
                received_at=submission.received_at,
            ),
        )
        await self.session.commit()
        return {"id": submission.id, "stage": submission.stage}

    async def erase_file(self, submission_id: uuid.UUID) -> dict[str, Any]:
        """Оригинал и миниатюру — досрочно, у всех скринов с этим файлом; поля и вердикты остаются.

        В S3 удаляет подписчик по событию; без S3 файл лежит только в staging и стирается сразу.
        """
        now = self.now()
        submission = await self.queries.submission(submission_id)
        if submission is None:
            raise not_found()
        leftovers: list[str] = []
        objects = await self.queries.file_objects(submission.file_id, for_update=True)
        for stored in objects:
            if stored.state in ("delete_requested", "deleted"):
                continue
            stored.updated_at = now
            # Выгрузка в полёте (`uploading`) — только через delete_requested: `deleted` здесь означал бы,
            # что объект в S3 точно не появится, а put может записать его следом (сверяет upload-подписчик).
            if len(stored.bucket) >= 3 or stored.state == "uploading":
                stored.state = "delete_requested"
                stored.delete_requested_at = now
                publish(self.session, delete_event(stored, "admin"))
            else:
                if stored.staging_path:
                    leftovers.append(stored.staging_path)
                stored.state = "deleted"
                stored.staging_path = None
                stored.deleted_at = now
        await self.session.commit()
        for path in leftovers:
            await self.staging.delete(path)
        return {"id": submission.id, "objects": [_object_out(stored) for stored in objects]}

    # --- конвейер ---

    async def pipeline(self, *, telegram_enabled: bool) -> dict[str, Any]:
        now = self.now()
        midnight = now.astimezone(self.tz).replace(hour=0, minute=0, second=0, microsecond=0)
        stages = await self.queries.stage_counts()
        oldest = await self.queries.oldest_undecided()
        p50, p95 = await self.queries.decision_latency(now - timedelta(days=1))
        hour_total, hour_failed, _ = await self.queries.extraction_stats(now - timedelta(hours=1))
        _, failed_today, tokens_today = await self.queries.extraction_stats(midnight)
        states, stored_bytes, oldest_staged = await self.queries.storage_stats()
        channels = await self.queries.channel_counts(midnight)
        return {
            "mode": self.config.mode,
            "telegram_enabled": telegram_enabled,
            "stages": {"processing": stages.get("processing", 0), "extracted": stages.get("extracted", 0)},
            "oldest_processing_age_s": _age(now, oldest),
            "p50_s": round(p50, 1) if p50 is not None else None,
            "p95_s": round(p95, 1) if p95 is not None else None,
            "vlm_error_rate_1h": round(hour_failed / hour_total, 4) if hour_total else None,
            "extraction_failed_today": failed_today,
            "tokens_today": tokens_today,
            "storage": {
                "objects": {state: states.get(state, 0) for state in STORAGE_STATES},
                "stored_bytes": stored_bytes,
                # Без S3 файлы ждут выгрузки по замыслу, а не из-за сбоя: возраст очереди не показываем.
                "oldest_staged_age_s": _age(now, oldest_staged) if self.images.blobs is not None else None,
                "staging_used_percent": await asyncio.to_thread(self.staging.used_percent),
            },
            "channels_today": {name: channels.get(name, 0) for name in ("extension", "telegram", "web")},
        }

    # --- Telegram ---

    async def telegram_links(self) -> list[dict[str, Any]]:
        users = await self.usernames()
        return [
            {
                "id": link.id,
                "user": {"id": link.user_id, "username": users.get(link.user_id, "")},
                "telegram_username": link.username,
                "linked_at": link.linked_at,
            }
            for link in await TelegramLinks(self.session).live()
        ]

    async def telegram_unlink(self, link_id: uuid.UUID, admin_id: uuid.UUID) -> None:
        if not await TelegramLinks(self.session).revoke(self.now(), by=admin_id, link_id=link_id):
            raise ServiceError("not_found", "Привязка не найдена")
        await self.session.commit()


_QUALITY = (
    "blurred",
    "cropped_missing_panel",
    "photo_of_screen",
    "moire_or_glare",
    "looks_edited",
    "dark_overlay_or_popup",
)


def _age(now: datetime, since: datetime | None) -> int | None:
    return max(0, int((now - since).total_seconds())) if since is not None else None


def _user_ref(user_id: uuid.UUID | None, users: dict[uuid.UUID, str]) -> dict[str, Any] | None:
    return {"id": user_id, "username": users.get(user_id, "")} if user_id is not None else None


def _object_out(stored: StorageObjectModel) -> dict[str, Any]:
    return {"kind": stored.kind, "state": stored.state, "stored_at": stored.stored_at, "deleted_at": stored.deleted_at}


def _extraction_out(extraction: ExtractionModel | None, attempts: int) -> dict[str, Any] | None:
    if extraction is None:
        return None
    return {
        "status": extraction.status,
        "pipeline_version": extraction.pipeline_version,
        "vlm_model": extraction.vlm_model,
        "prompt_version": extraction.vlm_prompt,
        "attempts": attempts,
        "error": extraction.error,
        "tokens_in": extraction.tokens_in,
        "tokens_out": extraction.tokens_out,
        "latency_ms": extraction.latency_ms,
        "created_at": extraction.created_at,
    }


def _event_out(event: VerdictEventModel, users: dict[uuid.UUID, str]) -> dict[str, Any]:
    kind, _, raw_id = event.actor.partition(":")
    actor_id = _uuid(raw_id)
    return {
        "at": event.at,
        "actor": {
            "kind": kind if kind in ("system", "user", "admin") else "system",
            "user": _user_ref(actor_id, users),
        },
        "from_verdict": event.from_verdict,
        "to_verdict": event.to_verdict,
        "reasons": reasons_out(event.reasons),
        "note": event.note,
    }
