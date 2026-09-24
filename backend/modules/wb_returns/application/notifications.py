"""Что и когда уходит в бот по возвратам.

Мгновенно: возврат стал «готов к выдаче». О новых заявках покупателей не пишем —
их разбирают в кабинете WB; бот напоминает только о сроке ответа.
По расписанию: утренний дайджест того, что едет в ПВЗ; напоминания на 3-й и
5-й день хранения; «завтра истекает срок ответа» по заявке.

Сообщение — событие в outbox с кодом бота и аудиторией «подписчики селлера»;
что уже отправлялось, помнит журнал модуля, чтобы повтор прохода не слал то же.
"""

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.notifications.application import BotNotFoundError, BotRegistry
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_returns.application.extension import STATE_NEEDS_LOGIN, ExtensionService
from backend.modules.wb_returns.domain import (
    CLAIM_REVIEW_DAYS,
    FREE_STORAGE_DAYS,
    RETURN_READY,
    RETURN_TRANSIT,
    STORAGE_DAYS,
)
from backend.modules.wb_returns.infrastructure.postgres import ClaimModel, ReturnModel, ReturnsRepository
from backend.shared.kafka_streams.topics import NotificationTopics
from backend.shared.outbox import OutboxRepository

TEMPLATE_READY = "returns.ready"
TEMPLATE_TRANSIT = "returns.transit"
TEMPLATE_REMINDER = "returns.reminder"
TEMPLATE_CLAIM_DEADLINE = "returns.claim_deadline"
TEMPLATE_CODE_MISSING = "returns.code_missing"
TEMPLATE_NEEDS_LOGIN = "returns.needs_login"
TEMPLATE_INSTALL_SILENT = "returns.install_silent"

KIND_READY = "ready"
KIND_TRANSIT = "transit"
KIND_REMINDER = "reminder"
KIND_CLAIM_DEADLINE = "claim_deadline"
KIND_CODE_MISSING = "code_missing"
KIND_NEEDS_LOGIN = "needs_login"
KIND_INSTALL_SILENT = "install_silent"

# На какие дни хранения напоминать: до начала платного хранения и за два дня до утилизации.
REMINDER_DAYS = (FREE_STORAGE_DAYS, STORAGE_DAYS - 2)
# Сообщение в Telegram — 4096 символов; длиннее список всё равно никто не читает.
ITEMS_LIMIT = 40


@dataclass(frozen=True, slots=True)
class NotificationReport:
    ready: int = 0
    transit: int = 0
    reminders: int = 0
    claim_deadlines: int = 0
    extension: int = 0

    @property
    def sent(self) -> int:
        return self.ready + self.transit + self.reminders + self.claim_deadlines + self.extension


class NotificationService:
    def __init__(
        self,
        session: AsyncSession,
        sellers: SellerRepository,
        returns: ReturnsRepository,
        bots: BotRegistry,
        extension: ExtensionService,
        *,
        bot_code: str,
        timezone: ZoneInfo,
        digest_hour: int,
        digest_minute: int,
        code_alert_hour: int = 1,
        install_silent_hours: int = 24,
    ) -> None:
        self.session = session
        self.sellers = sellers
        self.returns = returns
        self.bots = bots
        self.extension = extension
        self.bot_code = bot_code
        self.timezone = timezone
        self.digest_hour = digest_hour
        self.digest_minute = digest_minute
        self.code_alert_hour = code_alert_hour
        self.install_silent = timedelta(hours=install_silent_hours)
        self.logger = logging.getLogger("wb.returns.notifications")

    async def notify(self, seller_id: uuid.UUID, *, now: datetime) -> NotificationReport:
        try:
            await self.bots.by_code(self.bot_code)
        except BotNotFoundError:
            # Без зарегистрированного бота ничего не доставится. Журнал не
            # трогаем: как только бот появится, следующий проход отправит всё.
            self.logger.error("returns_bot_missing", extra={"seller_id": str(seller_id), "bot": self.bot_code})
            return NotificationReport()
        seller = await self.sellers.get(seller_id)
        seller_name = seller.name if seller else "магазин"

        ready_items = await self.returns.active_returns(seller_id, status_key=RETURN_READY)
        report = NotificationReport(
            ready=await self._notify_ready(seller_id, seller_name, ready_items, now),
            transit=await self._notify_transit(seller_id, seller_name, now),
            reminders=await self._notify_reminders(seller_id, seller_name, ready_items, now),
            claim_deadlines=await self._notify_claim_deadlines(seller_id, seller_name, now),
            extension=await self._notify_extension(seller_id, seller_name, now),
        )
        await self.session.commit()
        return report

    # --- returns ----------------------------------------------------------

    async def _notify_ready(
        self, seller_id: uuid.UUID, seller_name: str, items: list[ReturnModel], now: datetime
    ) -> int:
        logged = await self.returns.logged_keys(seller_id, KIND_READY, (str(item.shk_id) for item in items))
        fresh = [item for item in items if str(item.shk_id) not in logged]
        if not fresh:
            return 0
        code = await self.extension.code_params(seller_id, seller_name, self.extension.today(now))
        self._publish(
            seller_id,
            TEMPLATE_READY,
            dedupe=f"returns:ready:{seller_id}:{_digest(item.shk_id for item in fresh)}",
            params={
                "seller_name": seller_name,
                "total": len(fresh),
                "offices": self._offices(fresh, now),
                "free_days": FREE_STORAGE_DAYS,
                "storage_days": STORAGE_DAYS,
                "code": code.get("code"),
                "code_date": code.get("date"),
                "qr": code.get("qr"),
                "qr_url": code.get("qr_url"),
            },
        )
        await self.returns.log_sent(seller_id, KIND_READY, (str(item.shk_id) for item in fresh), now=now)
        return 1

    async def _notify_transit(self, seller_id: uuid.UUID, seller_name: str, now: datetime) -> int:
        local = now.astimezone(self.timezone)
        if (local.hour, local.minute) < (self.digest_hour, self.digest_minute):
            return 0
        day_key = local.date().isoformat()
        if await self.returns.logged_keys(seller_id, KIND_TRANSIT, [day_key]):
            return 0
        items = await self.returns.active_returns(seller_id, status_key=RETURN_TRANSIT)
        if not items:
            return 0
        self._publish(
            seller_id,
            TEMPLATE_TRANSIT,
            dedupe=f"returns:transit:{seller_id}:{day_key}",
            params={
                "seller_name": seller_name,
                "date": day_key,
                "total": len(items),
                "offices": self._offices(items, now),
            },
        )
        await self.returns.log_sent(seller_id, KIND_TRANSIT, [day_key], now=now)
        return 1

    async def _notify_reminders(
        self, seller_id: uuid.UUID, seller_name: str, items: list[ReturnModel], now: datetime
    ) -> int:
        sent = 0
        for day in REMINDER_DAYS:
            due = [item for item in items if _ready_at(item) + timedelta(days=day) <= now]
            keys = {str(item.shk_id): f"{day}:{item.shk_id}" for item in due}
            logged = await self.returns.logged_keys(seller_id, KIND_REMINDER, keys.values())
            fresh = [item for item in due if keys[str(item.shk_id)] not in logged]
            if not fresh:
                continue
            self._publish(
                seller_id,
                TEMPLATE_REMINDER,
                dedupe=f"returns:reminder:{seller_id}:{day}:{_digest(item.shk_id for item in fresh)}",
                params={
                    "seller_name": seller_name,
                    "day": day,
                    "total": len(fresh),
                    "offices": self._offices(fresh, now),
                    "free_days": FREE_STORAGE_DAYS,
                    "storage_days": STORAGE_DAYS,
                },
            )
            await self.returns.log_sent(seller_id, KIND_REMINDER, (keys[str(item.shk_id)] for item in fresh), now=now)
            sent += 1
        return sent

    def _offices(self, items: list[ReturnModel], now: datetime) -> list[dict]:
        """Возвраты по адресам ПВЗ: менеджер едет в конкретный пункт, а не по списку стикеров."""
        grouped: dict[str, list[ReturnModel]] = {}
        for item in items:
            grouped.setdefault(item.dst_office_address or "ПВЗ не указан", []).append(item)
        offices = []
        for address, rows in sorted(grouped.items()):
            ready_at = min((_ready_at(row) for row in rows), default=now)
            offices.append(
                {
                    "address": address,
                    "count": len(rows),
                    "free_until": (ready_at + timedelta(days=FREE_STORAGE_DAYS))
                    .astimezone(self.timezone)
                    .date()
                    .isoformat(),
                    "deadline": (ready_at + timedelta(days=STORAGE_DAYS)).astimezone(self.timezone).date().isoformat(),
                    "items": [
                        {
                            "title": self.returns.to_item(row).title,
                            "sticker": row.sticker_id or str(row.shk_id),
                            "return_type": row.return_type,
                            "reason": row.reason,
                        }
                        for row in rows[:ITEMS_LIMIT]
                    ],
                }
            )
        return offices

    # --- claims -----------------------------------------------------------

    async def _notify_claim_deadlines(self, seller_id: uuid.UUID, seller_name: str, now: datetime) -> int:
        claims = await self.returns.open_claims(seller_id)
        # За сутки до автоодобрения: ещё есть время ответить, и это уже не «на днях».
        due = [claim for claim in claims if claim.dt + timedelta(days=CLAIM_REVIEW_DAYS - 1) <= now]
        logged = await self.returns.logged_keys(seller_id, KIND_CLAIM_DEADLINE, (str(c.claim_id) for c in due))
        fresh = [claim for claim in due if str(claim.claim_id) not in logged]
        if not fresh:
            return 0
        self._publish(
            seller_id,
            TEMPLATE_CLAIM_DEADLINE,
            dedupe=f"returns:claim-deadline:{seller_id}:{_digest(claim.claim_id for claim in fresh)}",
            params={
                "seller_name": seller_name,
                "total": len(fresh),
                "claims": [self._claim(claim) for claim in fresh[:ITEMS_LIMIT]],
            },
        )
        await self.returns.log_sent(seller_id, KIND_CLAIM_DEADLINE, (str(c.claim_id) for c in fresh), now=now)
        return 1

    def _claim(self, model: ClaimModel) -> dict:
        claim = self.returns.to_claim(model)
        return {
            "name": claim.imt_name or f"артикул {claim.nm_id}",
            "nm_id": claim.nm_id,
            "price": claim.price,
            "comment": claim.user_comment,
            "photos": list(claim.photo_urls[:3]),
            "created": claim.dt.astimezone(self.timezone).isoformat(),
            "deadline": (claim.dt + timedelta(days=CLAIM_REVIEW_DAYS)).astimezone(self.timezone).isoformat(),
        }

    # --- extension --------------------------------------------------------

    async def _notify_extension(self, seller_id: uuid.UUID, seller_name: str, now: datetime) -> int:
        """Тревоги про расширение: нет кода к утру, слетела сессия, установка молчит."""
        installs = await self.returns.active_installs(seller_id)
        if not installs:
            return 0
        local = now.astimezone(self.timezone)
        day_key = local.date().isoformat()
        sent = 0
        code_due = local.hour >= self.code_alert_hour
        if (
            code_due
            and await self.returns.delivery_code(seller_id, local.date()) is None
            and not await self.returns.logged_keys(seller_id, KIND_CODE_MISSING, [day_key])
        ):
            self._publish(
                seller_id,
                TEMPLATE_CODE_MISSING,
                dedupe=f"returns:code-missing:{seller_id}:{day_key}",
                params={"seller_name": seller_name, "date": day_key, "installs": len(installs)},
            )
            await self.returns.log_sent(seller_id, KIND_CODE_MISSING, [day_key], now=now)
            sent += 1
        for install in installs:
            key = f"{install.id}:{day_key}"
            if install.state == STATE_NEEDS_LOGIN and not await self.returns.logged_keys(
                seller_id, KIND_NEEDS_LOGIN, [key]
            ):
                self._publish(
                    seller_id,
                    TEMPLATE_NEEDS_LOGIN,
                    dedupe=f"returns:needs-login:{key}",
                    params={"seller_name": seller_name, "browser": install.browser},
                )
                await self.returns.log_sent(seller_id, KIND_NEEDS_LOGIN, [key], now=now)
                sent += 1
            last_seen = install.last_seen_at or install.created_at
            if last_seen < now - self.install_silent and not await self.returns.logged_keys(
                seller_id, KIND_INSTALL_SILENT, [key]
            ):
                self._publish(
                    seller_id,
                    TEMPLATE_INSTALL_SILENT,
                    dedupe=f"returns:install-silent:{key}",
                    params={
                        "seller_name": seller_name,
                        "browser": install.browser,
                        "last_seen_at": last_seen.astimezone(self.timezone).isoformat(),
                        "hours": int(self.install_silent.total_seconds() // 3600),
                    },
                )
                await self.returns.log_sent(seller_id, KIND_INSTALL_SILENT, [key], now=now)
                sent += 1
        return sent

    # --- outbox -----------------------------------------------------------

    def _publish(self, seller_id: uuid.UUID, template: str, *, dedupe: str, params: dict) -> None:
        OutboxRepository(self.session).add(
            aggregate_id=seller_id,
            event_type="ReturnsNotificationRequested",
            topic=NotificationTopics.TELEGRAM_MESSAGE_REQUESTED,
            payload={
                "message_id": str(uuid.uuid4()),
                "bot": self.bot_code,
                "audience": {"type": "seller_subscribers", "seller_id": str(seller_id)},
                "template": template,
                "dedupe_key": dedupe,
                "params": params,
            },
        )


def _ready_at(item: ReturnModel) -> datetime:
    """От какого момента считать хранение: дата готовности WB, иначе — когда мы её увидели."""
    return item.ready_to_return_dt or item.status_changed_at


def _digest(keys) -> str:  # noqa: ANN001 — любые ключи, приводимые к строке
    joined = ",".join(sorted(str(key) for key in keys))
    return hashlib.sha1(joined.encode()).hexdigest()[:16]  # noqa: S324 — не для безопасности, ключ дедупликации
