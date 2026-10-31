"""Бот приёма скриншотов в Telegram: апдейт из `notifications.telegram.update.received` → действия.

Состояние не хранится отдельно, а выводится из данных: привязка — `telegram_links`, ожидание исхода —
скрин канала `telegram` без `outcome`. Разбор апдейта (`classify`), отказ по доступу (`refusal`) и
кнопки исхода — чистые функции; `TelegramBotService` читает состояние, делает приём и шлёт ответы.

Канал идёт мимо HTTP и `require_service`, поэтому каждый апдейт сам проверяет: привязка жива,
сотрудник активен и ему выдан сервис `wb-screenshot-control`. Отказ не раскрывает, что именно не так.

Повтор апдейта отсекают три слоя: курсор модуля уведомлений, `inbox_events` по uuid5 от
`(бот, update_id)` и `UNIQUE(user_id, client_submission_id)` с `tg:<update_id>:<n>`. Ответы уходят
событиями `message.requested` с ключом дедупа от `update_id`, так что повтор не шлёт второе сообщение.
"""

import base64
import binascii
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.notifications.application import MessengerFiles
from backend.modules.notifications.application import templates as messages
from backend.modules.notifications.domain import (
    CALLBACK_ANSWER_KIND,
    EDIT_KIND,
    MESSAGE_KIND,
    UPDATE_CALLBACK,
    Attachment,
    MessengerFileTooLargeError,
    MessengerPermanentError,
    UpdateEvent,
)
from backend.modules.platform.application import ServiceAccessService
from backend.modules.platform.domain import User
from backend.modules.platform.infrastructure.postgres import UserRepository
from backend.modules.wb_screenshot_control.application.automation import AUTOMATION_ID
from backend.modules.wb_screenshot_control.application.errors import ServiceError
from backend.modules.wb_screenshot_control.application.events import ScreenshotVerdictReady
from backend.modules.wb_screenshot_control.application.images import ImageLinks
from backend.modules.wb_screenshot_control.application.inbox import InvalidEventError, parse_event
from backend.modules.wb_screenshot_control.application.ingest import IngestError, IngestService
from backend.modules.wb_screenshot_control.application.kpi import KpiService
from backend.modules.wb_screenshot_control.application.periods import resolve_period
from backend.modules.wb_screenshot_control.application.submissions_service import (
    SubmissionsService,
    manager_status,
    reason_codes,
)
from backend.modules.wb_screenshot_control.application.telegram_link import hash_link_token
from backend.modules.wb_screenshot_control.domain import IMAGE_TYPES, OUTCOMES
from backend.modules.wb_screenshot_control.domain.manager_reasons import manager_reason
from backend.modules.wb_screenshot_control.domain.ports import Staging
from backend.modules.wb_screenshot_control.infrastructure.postgres.models import TelegramLinkModel
from backend.modules.wb_screenshot_control.infrastructure.postgres.queries import ScreenshotQueries, Window
from backend.modules.wb_screenshot_control.infrastructure.postgres.repository import ScreenshotRepository
from backend.modules.wb_screenshot_control.infrastructure.postgres.telegram import TelegramStore
from backend.modules.wb_screenshot_control.infrastructure.postgres.user_settings import UserSettingsRepository
from backend.shared.kafka_streams.topics import NotificationTopics
from backend.shared.outbox import OutboxRepository
from backend.shared.settings.config import ScreenshotControlConfig
from backend.storage.pg import Database

EVENT_TELEGRAM_UPDATE = "TelegramUpdateReceived"
EVENT_VERDICT_SENT = "ScreenshotVerdictTelegram"
MAX_FILES_PER_UPDATE = 10
LAST_LIMIT = 5
ACTION_PREFIX = "sc:o:"
OUTCOME_CODES: dict[str, str] = {"positive": "p", "amended": "a", "deleted": "d"}
OUTCOME_BY_CODE = {code: outcome for outcome, code in OUTCOME_CODES.items()}
# Короткие подписи кнопок: длинные из OUTCOME_LABELS не помещаются в ряд на телефоне.
BUTTON_LABELS: dict[str, str] = {"positive": "Положительный", "amended": "Изменён", "deleted": "Удалён"}
OUTCOME_TEXT: dict[str, str] = {
    "positive": "отзыв положительный",
    "amended": "отзыв изменён на положительный",
    "deleted": "отзыв удалён",
}
STATUS_TEXT = {"processing": "обрабатывается", "awaiting_outcome": "ждёт исхода"}
# Ошибки приёма, о которых менеджеру говорится своими словами (`screens.rejected_file`).
REJECT_REASONS = {"file_too_large", "too_small", "unsupported_type", "bad_image", "rate_limited", "no_file"}

Intent = Literal["start", "command", "files", "callback", "no_file"]


# --- чистые функции ---


@dataclass(frozen=True, slots=True)
class Classified:
    intent: Intent
    command: str = ""
    argument: str = ""


def classify(event: UpdateEvent) -> Classified:
    """Что прислали: нажатие, команду, файлы или что-то без файла."""
    if event.kind == UPDATE_CALLBACK:
        return Classified("callback")
    text = event.text.strip()
    if text.startswith("/"):
        command, _, argument = text.partition(" ")
        command = command.partition("@")[0][1:].lower()
        if command == "start":
            return Classified("start", "start", argument.strip())
        return Classified("command", command, argument.strip())
    if event.attachments:
        return Classified("files")
    return Classified("no_file")


@dataclass(frozen=True, slots=True)
class Access:
    """Состояние отправителя, прочитанное из базы: привязка, активность и выданный сервис."""

    link: TelegramLinkModel | None
    user: User | None
    allowed: bool


def refusal(event: UpdateEvent, access: Access) -> str | None:
    """Шаблон отказа или `None`. Заблокированный и лишённый сервиса слышат одно и то же."""
    if event.chat_type and event.chat_type != "private":
        return messages.SCREENS_PRIVATE_ONLY
    if access.link is None:
        return messages.SCREENS_NOT_LINKED
    if access.user is None or not access.user.is_active or not access.allowed:
        return messages.SCREENS_NO_ACCESS
    return None


def outcome_action(submission_id: uuid.UUID, outcome: str) -> str:
    """`sc:o:<id в base64url без паддинга>:<p|a|d>` — 29 байт при лимите 64."""
    ref = base64.urlsafe_b64encode(submission_id.bytes).decode().rstrip("=")
    return f"{ACTION_PREFIX}{ref}:{OUTCOME_CODES[outcome]}"


def parse_outcome_action(data: str) -> tuple[uuid.UUID, str] | None:
    if not data.startswith(ACTION_PREFIX):
        return None
    ref, _, code = data[len(ACTION_PREFIX) :].partition(":")
    outcome = OUTCOME_BY_CODE.get(code)
    if outcome is None or len(ref) != 22:
        return None
    try:
        raw = base64.urlsafe_b64decode(ref + "==")
    except (binascii.Error, ValueError):
        return None
    if len(raw) != 16:
        return None
    return uuid.UUID(bytes=raw), outcome


def outcome_buttons(submission_id: uuid.UUID) -> list[list[dict[str, str]]]:
    return [
        [{"label": BUTTON_LABELS[outcome], "action": outcome_action(submission_id, outcome)} for outcome in OUTCOMES]
    ]


def update_inbox_id(bot_code: str, update_id: int) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"telegram-update:{bot_code}:{update_id}")


def chat_aggregate(chat_id: int) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"telegram-chat:{chat_id}")


# --- ответы ---


@dataclass(frozen=True, slots=True)
class Reply:
    template: str
    params: dict[str, Any] = field(default_factory=dict)
    key: str = "reply"
    kind: str = MESSAGE_KIND
    target_ref: str | None = None


def publish_reply(session: AsyncSession, *, bot_code: str, chat_id: int, dedupe: str, reply: Reply) -> None:
    payload: dict[str, Any] = {
        "message_id": str(uuid.uuid4()),
        "bot": bot_code,
        "audience": {"type": "chat", "chat_id": chat_id},
        "template": reply.template,
        "dedupe_key": dedupe,
        "params": reply.params,
    }
    if reply.kind != MESSAGE_KIND:
        payload["kind"] = reply.kind
        payload["target_ref"] = reply.target_ref
    OutboxRepository(session).add(
        aggregate_id=chat_aggregate(chat_id),
        event_type="ScreenshotTelegramReply",
        topic=NotificationTopics.TELEGRAM_MESSAGE_REQUESTED,
        payload=payload,
    )


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TelegramBotService:
    def __init__(
        self,
        database: Database,
        staging: Staging,
        files: MessengerFiles,
        config: ScreenshotControlConfig,
        *,
        bucket: str,
        now: Any = _utcnow,
    ) -> None:
        self.database = database
        self.staging = staging
        self.files = files
        self.config = config
        self.bucket = bucket
        self.now = now
        self.logger = structlog.get_logger("wb_screenshot_control.telegram")

    # --- апдейты ---

    async def handle(self, payload: dict[str, Any]) -> None:
        event = self._parse(payload)
        if event is None:
            return
        inbox_id = update_inbox_id(event.bot_code, event.update_id)
        async with self.database.session() as session:
            repository = ScreenshotRepository(session)
            if await repository.inbox_processed(inbox_id):
                return
            replies = await self._process(session, event)
            for reply in replies:
                publish_reply(
                    session,
                    bot_code=event.bot_code,
                    chat_id=event.chat_id,
                    dedupe=f"screens:{event.update_id}:{reply.key}",
                    reply=reply,
                )
            repository.mark_inbox(inbox_id, EVENT_TELEGRAM_UPDATE)
            await session.commit()
        self.logger.info(
            "screenshot_telegram_update",
            update_id=event.update_id,
            replies=[reply.template for reply in replies],
        )

    async def park(self, payload: dict[str, Any], error: str) -> None:
        """Апдейт, на котором обработчик падал раз за разом (например, файл не скачать): сказать и закрыть."""
        event = self._parse(payload)
        if event is None:
            return
        inbox_id = update_inbox_id(event.bot_code, event.update_id)
        async with self.database.session() as session:
            repository = ScreenshotRepository(session)
            if await repository.inbox_processed(inbox_id):
                return
            reply = (
                Reply(
                    messages.SCREENS_CALLBACK, {"result": "failed"}, "parked", CALLBACK_ANSWER_KIND, event.callback.id
                )
                if event.callback
                else Reply(
                    messages.SCREENS_REJECTED_FILE, {"reason": "temporary", "reply_to": event.message_ref}, "parked"
                )
            )
            publish_reply(
                session,
                bot_code=event.bot_code,
                chat_id=event.chat_id,
                dedupe=f"screens:{event.update_id}:parked",
                reply=reply,
            )
            repository.mark_inbox(inbox_id, EVENT_TELEGRAM_UPDATE)
            await session.commit()
        self.logger.error("screenshot_telegram_update_parked", update_id=event.update_id, error=error)

    def _parse(self, payload: dict[str, Any]) -> UpdateEvent | None:
        try:
            event = UpdateEvent.parse(payload)
        except (ValueError, TypeError) as error:
            raise InvalidEventError(str(error)) from error
        if event.bot_code != self.config.notification_bot:
            return None
        return event

    async def _process(self, session: AsyncSession, event: UpdateEvent) -> list[Reply]:
        intent = classify(event)
        if intent.intent == "start" and intent.argument:
            return await self._link(session, event, intent.argument)
        access = await self._access(session, event.user_id)
        refused = refusal(event, access)
        if refused is not None:
            if intent.intent == "callback" and event.callback:
                return [self._answer(event, "no_access")]
            return [Reply(refused)]
        assert access.link is not None and access.user is not None
        user_id = access.user.id
        if intent.intent == "callback":
            return await self._callback(session, event, user_id)
        if intent.intent == "files":
            return await self._files(session, event, user_id)
        if intent.intent == "no_file":
            return [Reply(messages.SCREENS_REJECTED_FILE, {"reason": "no_file", "reply_to": event.message_ref})]
        if intent.intent == "start":
            return [Reply(messages.SCREENS_LINKED, {"username": access.user.username, "already": True})]
        return await self._command(session, intent.command, access.link, access.user)

    async def _access(self, session: AsyncSession, telegram_user_id: int | None) -> Access:
        if telegram_user_id is None:
            return Access(None, None, False)
        link = await TelegramStore(session).link_by_telegram(telegram_user_id)
        if link is None:
            return Access(None, None, False)
        users = UserRepository(session)
        user = await users.get_by_id(link.user_id)
        allowed = user is not None and await ServiceAccessService(users).allows(user, AUTOMATION_ID)
        return Access(link, user, allowed)

    # --- привязка ---

    async def _link(self, session: AsyncSession, event: UpdateEvent, token: str) -> list[Reply]:
        if event.chat_type and event.chat_type != "private":
            return [Reply(messages.SCREENS_PRIVATE_ONLY)]
        if event.user_id is None:
            return [Reply(messages.SCREENS_LINK_INVALID)]
        store = TelegramStore(session)
        now = self.now()
        claimed = await store.claim_token(hash_link_token(token), now) if len(token) <= 64 else None
        if claimed is None:
            return [Reply(messages.SCREENS_LINK_INVALID)]
        users = UserRepository(session)
        user = await users.get_by_id(claimed.user_id)
        if user is None or not user.is_active or not await ServiceAccessService(users).allows(user, AUTOMATION_ID):
            return [Reply(messages.SCREENS_NO_ACCESS)]  # токен сгорает и у заблокированного
        by_telegram = await store.link_by_telegram(event.user_id)
        if by_telegram is not None:
            if by_telegram.user_id == user.id:
                return [Reply(messages.SCREENS_LINKED, {"username": user.username, "already": True})]
            return [Reply(messages.SCREENS_LINK_TAKEN, {"reason": "telegram"})]
        if await store.link_of_user(user.id) is not None:
            return [Reply(messages.SCREENS_LINK_TAKEN, {"reason": "staff"})]
        link = await store.add_link(
            user_id=user.id, telegram_user_id=event.user_id, chat_id=event.chat_id, username=event.username
        )
        if link is None:
            return [Reply(messages.SCREENS_LINK_TAKEN, {"reason": "telegram"})]
        self.logger.info("screenshot_telegram_linked", user_id=str(user.id))
        return [Reply(messages.SCREENS_LINKED, {"username": user.username})]

    # --- команды ---

    async def _command(self, session: AsyncSession, command: str, link: TelegramLinkModel, user: User) -> list[Reply]:
        if command == "summary":
            return [await self._summary(session, user.id)]
        if command == "last":
            return [await self._last(session, user.id)]
        if command == "unlink":
            await TelegramStore(session).revoke(link, self.now(), by=user.id)
            return [Reply(messages.SCREENS_UNLINKED)]
        return [Reply(messages.SCREENS_HELP)]

    async def _summary(self, session: AsyncSession, user_id: uuid.UUID) -> Reply:
        today = self.now().astimezone(ZoneInfo(self.config.timezone)).date()
        summary = await KpiService(session, self.config).summary(user_id, resolve_period(None, None, today=today))
        counts = summary["counts"]
        return Reply(
            messages.SCREENS_SUMMARY,
            {
                "date": today.isoformat(),
                "positive": counts["positive"],
                "amended": counts["amended"],
                "deleted": counts["deleted"],
                "accepted_total": counts["accepted_total"],
                "pending_review": summary["pending_review"],
                "awaiting_outcome": summary["awaiting_outcome"],
                "processing": summary["processing"],
                "duplicates": summary["duplicates"],
                "rejected": summary["rejected"],
                "preliminary": summary["preliminary"],
            },
        )

    async def _last(self, session: AsyncSession, user_id: uuid.UUID) -> Reply:
        rows = await ScreenshotQueries(session).own_submissions(user_id, window=Window(), limit=LAST_LIMIT)
        zone = ZoneInfo(await UserSettingsRepository(session).timezone(user_id) or self.config.timezone)
        items = []
        for row in rows:
            reason = manager_reason(row.verdict, reason_codes(row))
            status = manager_status(row)
            items.append(
                {
                    "at": row.received_at.astimezone(zone).strftime("%d.%m %H:%M"),
                    "outcome_label": OUTCOME_TEXT.get(row.outcome or ""),
                    "status_text": reason.text if reason else STATUS_TEXT.get(status, status),
                }
            )
        return Reply(messages.SCREENS_LAST, {"items": items})

    # --- нажатие кнопки исхода ---

    def _answer(self, event: UpdateEvent, result: str) -> Reply:
        assert event.callback is not None
        return Reply(messages.SCREENS_CALLBACK, {"result": result}, "answer", CALLBACK_ANSWER_KIND, event.callback.id)

    async def _callback(self, session: AsyncSession, event: UpdateEvent, user_id: uuid.UUID) -> list[Reply]:
        assert event.callback is not None
        parsed = parse_outcome_action(event.callback.data)
        if parsed is None:
            return [self._answer(event, "not_found")]
        submission_id, outcome = parsed
        # Миниатюры боту не нужны: карточка после смены исхода не показывается.
        service = SubmissionsService(session, self.config, ImageLinks(None, 0), now=self.now)
        try:
            await service.change_outcome(user_id, submission_id, outcome, source="button")
        except ServiceError as error:
            await session.rollback()
            return [self._answer(event, "locked" if error.code == "outcome_locked" else "not_found")]
        replies = [self._answer(event, "saved")]
        target = event.callback.message_ref or event.message_ref
        if target.isdigit():
            replies.append(
                Reply(
                    messages.SCREENS_OUTCOME_SET,
                    {"outcome_label": OUTCOME_TEXT[outcome]},
                    "edit",
                    EDIT_KIND,
                    target,
                )
            )
        return replies

    # --- файлы ---

    async def _files(self, session: AsyncSession, event: UpdateEvent, user_id: uuid.UUID) -> list[Reply]:
        replies: list[Reply] = []
        limited: bool | None = None
        ingest = IngestService(session, self.staging, self.config, bucket=self.bucket)
        repository = ScreenshotRepository(session)
        for index, attachment in enumerate(event.attachments[:MAX_FILES_PER_UPDATE]):
            key = f"file:{index}"
            client_submission_id = f"tg:{event.update_id}:{index}"
            known = await repository.submission_by_client_id(user_id, client_submission_id)
            if known is not None:  # повтор апдейта: файл уже принят, только ответить
                replies.append(self._received(event, known[0].id, known[0].verdict is not None, key))
                continue
            refused = self._precheck(attachment)
            if refused is None:
                if limited is None:
                    limited = await self._rate_limited(session, user_id)
                if limited:
                    refused = "rate_limited"
            if refused is not None:
                replies.append(self._rejected(event, refused, key))
                continue
            try:
                data = await self.files.fetch(
                    event.bot_code, attachment.file_ref, max_bytes=self.config.max_upload_bytes
                )
            except MessengerFileTooLargeError:
                replies.append(self._rejected(event, "file_too_large", key))
                continue
            except MessengerPermanentError as error:
                self.logger.warning("screenshot_telegram_file_unavailable", update_id=event.update_id, error=str(error))
                replies.append(self._rejected(event, "unavailable", key))
                continue
            try:
                result = await ingest.ingest(
                    user_id,
                    "telegram",
                    data,
                    client_submission_id=client_submission_id,
                    client_meta={
                        "telegram_compressed": attachment.kind == "photo",
                        "telegram_forwarded": event.forwarded,
                        "telegram_kind": attachment.kind,
                    },
                    caption_raw=event.caption or None,
                    tg_chat_id=event.chat_id,
                    tg_message_id=int(event.message_ref) if event.message_ref.isdigit() else None,
                    tg_media_group_id=event.media_group_id or None,
                )
            except IngestError as error:
                replies.append(self._rejected(event, error.code, key))
                continue
            replies.append(self._received(event, result.submission_id, result.verdict is not None, key))
        return replies

    def _precheck(self, attachment: Attachment) -> str | None:
        """Отказ до скачивания: не картинка или заведомо больше лимита. Сигнатуру потом проверит приём."""
        if attachment.kind not in ("photo", "document"):
            return "unsupported_type"
        if attachment.kind == "document" and attachment.mime and attachment.mime not in IMAGE_TYPES:
            return "unsupported_type"
        if attachment.size is not None and attachment.size > self.config.max_upload_bytes:
            return "file_too_large"
        return None

    async def _rate_limited(self, session: AsyncSession, user_id: uuid.UUID) -> bool:
        """Те же бюджеты, что у веба и расширения (`uploads_per_minute/hour`), но по базе: у воркера нет лимитера."""
        store = TelegramStore(session)
        now = self.now()
        if await store.uploads_since(user_id, now - timedelta(minutes=1)) >= self.config.uploads_per_minute:
            return True
        return await store.uploads_since(user_id, now - timedelta(hours=1)) >= self.config.uploads_per_hour

    def _received(self, event: UpdateEvent, submission_id: uuid.UUID, decided: bool, key: str) -> Reply:
        params: dict[str, Any] = {"decided": decided, "reply_to": event.message_ref}
        if not decided:
            params["buttons"] = outcome_buttons(submission_id)
        return Reply(messages.SCREENS_RECEIVED, params, key)

    def _rejected(self, event: UpdateEvent, code: str, key: str) -> Reply:
        reason = code if code in REJECT_REASONS else "unavailable"
        return Reply(
            messages.SCREENS_REJECTED_FILE,
            {
                "reason": reason,
                "max_mb": self.config.max_upload_bytes // (1024 * 1024),
                "min_side": self.config.min_side_px,
                "reply_to": event.message_ref,
            },
            key,
        )

    # --- вердикт ---

    async def verdict(self, payload: dict[str, Any]) -> None:
        """`ScreenshotVerdictReady` скрина из Telegram → короткая фраза ответом на сообщение менеджера.

        Пишем только в живую привязку того же чата, активному сотруднику с сервисом. Переоценка админом —
        новое сообщение; тот же вердикт ещё раз — молчим.
        """
        event, event_id = parse_event(ScreenshotVerdictReady, payload)
        if event.channel != "telegram" or event.previous_verdict == event.verdict:
            return
        async with self.database.session() as session:
            repository = ScreenshotRepository(session)
            if await repository.inbox_processed(event_id):
                return
            submission = await repository.submission(event.submission_id)
            send = submission is not None and submission.tg_chat_id is not None
            if send and submission is not None:
                link = await TelegramStore(session).link_of_user(submission.user_id)
                users = UserRepository(session)
                user = await users.get_by_id(submission.user_id)
                send = (
                    link is not None
                    and link.chat_id == submission.tg_chat_id
                    and user is not None
                    and user.is_active
                    and await ServiceAccessService(users).allows(user, AUTOMATION_ID)
                )
                if send:
                    reason = manager_reason(event.verdict, reason_codes(submission))
                    publish_reply(
                        session,
                        bot_code=self.config.notification_bot,
                        chat_id=int(submission.tg_chat_id),  # type: ignore[arg-type]
                        dedupe=f"screens:verdict:{event_id}",
                        reply=Reply(
                            messages.SCREENS_VERDICT,
                            {
                                "verdict": event.verdict,
                                "reason_text": reason.text if reason else "",
                                "reply_to": str(submission.tg_message_id or ""),
                            },
                        ),
                    )
            repository.mark_inbox(event_id, EVENT_VERDICT_SENT)
            await session.commit()
