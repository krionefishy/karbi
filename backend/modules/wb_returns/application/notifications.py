"""Что и когда уходит в бот по возвратам.

Ничего не шлётся мгновенно: события копятся и уходят одним сообщением на кабинет
в слоты `notify_hours` (09:00, 12:00, 15:00, 18:00 МСК), и только если с прошлого
слота что-то изменилось — появились готовые к выдаче или едущие возвраты, кто-то
лежит в ПВЗ дольше бесплатного срока, расширение просит внимания. Само сообщение
короткое: код дня с QR и адреса ПВЗ с количеством. О заявках покупателей бот не
пишет — их разбирают в кабинете WB.

Сообщение — событие в outbox с кодом бота и аудиторией «подписчики селлера»;
что уже отправлялось, помнит журнал модуля, чтобы повтор прохода не слал то же.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.notifications.application import BotNotFoundError, BotRegistry
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_returns.application.extension import STATE_NEEDS_LOGIN, ExtensionService
from backend.modules.wb_returns.domain import FREE_STORAGE_DAYS, RETURN_READY, RETURN_TRANSIT
from backend.modules.wb_returns.infrastructure.postgres import ReturnModel, ReturnsRepository
from backend.shared.kafka_streams.topics import NotificationTopics
from backend.shared.outbox import OutboxRepository

TEMPLATE_DIGEST = "returns.digest"

KIND_SLOT = "slot"
KIND_READY = "ready"
KIND_TRANSIT = "transit"
KIND_OVERDUE = "overdue"
KIND_CODE_MISSING = "code_missing"
KIND_NEEDS_LOGIN = "needs_login"
KIND_INSTALL_SILENT = "install_silent"


@dataclass(frozen=True, slots=True)
class NotificationReport:
    digest: int = 0
    ready: int = 0
    transit: int = 0
    overdue: int = 0
    alerts: int = 0

    @property
    def sent(self) -> int:
        return self.digest


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
        notify_hours: tuple[int, ...] = (9, 12, 15, 18),
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
        self.notify_hours = tuple(sorted(notify_hours))
        self.code_alert_hour = code_alert_hour
        self.install_silent = timedelta(hours=install_silent_hours)
        self.logger = logging.getLogger("wb.returns.notifications")

    def due_slot(self, now: datetime) -> int | None:
        """Последний наступивший слот сегодня. Пропущенные за простой воркера не догоняются:
        сообщение и так собирает всё накопившееся."""
        local = now.astimezone(self.timezone)
        passed = [hour for hour in self.notify_hours if local.hour >= hour]
        return max(passed) if passed else None

    async def notify(self, seller_id: uuid.UUID, *, now: datetime) -> NotificationReport:
        slot = self.due_slot(now)
        if slot is None:
            return NotificationReport()
        local = now.astimezone(self.timezone)
        day_key = local.date().isoformat()
        slot_key = f"{day_key}:{slot:02d}"
        if await self.returns.logged_keys(seller_id, KIND_SLOT, [slot_key]):
            return NotificationReport()
        try:
            await self.bots.by_code(self.bot_code)
        except BotNotFoundError:
            # Без зарегистрированного бота ничего не доставится. Журнал не
            # трогаем: как только бот появится, следующий слот отправит всё.
            self.logger.error("returns_bot_missing", extra={"seller_id": str(seller_id), "bot": self.bot_code})
            return NotificationReport()
        seller = await self.sellers.get(seller_id)
        seller_name = seller.name if seller else "магазин"

        ready = await self.returns.active_returns(seller_id, status_key=RETURN_READY)
        transit = await self.returns.active_returns(seller_id, status_key=RETURN_TRANSIT)
        fresh_ready = await self._fresh(seller_id, KIND_READY, {str(item.shk_id): item for item in ready})
        fresh_transit = await self._fresh(seller_id, KIND_TRANSIT, {str(item.shk_id): item for item in transit})
        overdue = [item for item in ready if _ready_at(item) + timedelta(days=FREE_STORAGE_DAYS) <= now]
        fresh_overdue = await self._fresh(seller_id, KIND_OVERDUE, {str(item.shk_id): item for item in overdue})
        alerts, alert_keys = await self._alerts(seller_id, now)

        report = NotificationReport(
            ready=len(fresh_ready), transit=len(fresh_transit), overdue=len(fresh_overdue), alerts=len(alerts)
        )
        if fresh_ready or fresh_transit or fresh_overdue or alerts:
            code = await self.extension.code_params(seller_id, seller_name, local.date())
            self._publish(
                seller_id,
                TEMPLATE_DIGEST,
                dedupe=f"returns:digest:{seller_id}:{slot_key}",
                params={
                    "seller_name": seller_name,
                    "date": day_key,
                    "code": code.get("code"),
                    "qr": code.get("qr"),
                    "no_install": bool(code.get("no_install")),
                    "ready": _by_office(ready),
                    "ready_total": len(ready),
                    "transit": _by_office(transit),
                    "transit_total": len(transit),
                    "overdue_total": len(overdue),
                    "free_days": FREE_STORAGE_DAYS,
                    "alerts": alerts,
                },
            )
            await self.returns.log_sent(seller_id, KIND_READY, fresh_ready, now=now)
            await self.returns.log_sent(seller_id, KIND_TRANSIT, fresh_transit, now=now)
            await self.returns.log_sent(seller_id, KIND_OVERDUE, fresh_overdue, now=now)
            for kind, keys in alert_keys.items():
                await self.returns.log_sent(seller_id, kind, keys, now=now)
            report = NotificationReport(
                digest=1, ready=report.ready, transit=report.transit, overdue=report.overdue, alerts=report.alerts
            )
        await self.returns.log_sent(seller_id, KIND_SLOT, [slot_key], now=now)
        await self.session.commit()
        return report

    async def _fresh(self, seller_id: uuid.UUID, kind: str, items: dict[str, ReturnModel]) -> list[str]:
        logged = await self.returns.logged_keys(seller_id, kind, items.keys())
        return [key for key in items if key not in logged]

    async def _alerts(self, seller_id: uuid.UUID, now: datetime) -> tuple[list[str], dict[str, list[str]]]:
        """Строки про расширение, которые ещё не показывали сегодня: нет кода, слетел вход, молчит."""
        installs = await self.returns.active_installs(seller_id)
        if not installs:
            return [], {}
        local = now.astimezone(self.timezone)
        day_key = local.date().isoformat()
        alerts: list[str] = []
        keys: dict[str, list[str]] = {KIND_CODE_MISSING: [], KIND_NEEDS_LOGIN: [], KIND_INSTALL_SILENT: []}
        if (
            local.hour >= self.code_alert_hour
            and await self.returns.delivery_code(seller_id, local.date()) is None
            and not await self.returns.logged_keys(seller_id, KIND_CODE_MISSING, [day_key])
        ):
            alerts.append(
                "Код на сегодня не пришёл: проверьте, что браузер с расширением включён и на WB выполнен вход"
            )
            keys[KIND_CODE_MISSING].append(day_key)
        for install in installs:
            key = f"{install.id}:{day_key}"
            browser = install.browser or "браузер"
            if install.state == STATE_NEEDS_LOGIN and not await self.returns.logged_keys(
                seller_id, KIND_NEEDS_LOGIN, [key]
            ):
                alerts.append(f"Слетел вход на wildberries.ru ({browser}): войдите заново под телефоном владельца")
                keys[KIND_NEEDS_LOGIN].append(key)
            last_seen = install.last_seen_at or install.created_at
            if last_seen < now - self.install_silent and not await self.returns.logged_keys(
                seller_id, KIND_INSTALL_SILENT, [key]
            ):
                hours = int(self.install_silent.total_seconds() // 3600)
                alerts.append(
                    f"Расширение ({browser}) молчит больше {hours} ч.: включите браузер или подключите заново"
                )
                keys[KIND_INSTALL_SILENT].append(key)
        return alerts, {kind: found for kind, found in keys.items() if found}

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


def _by_office(items: list[ReturnModel]) -> list[dict]:
    """Только адрес и сколько штук: менеджер едет в конкретный пункт, товар ему назовут на кассе."""
    counts: dict[str, int] = {}
    for item in items:
        address = item.dst_office_address or "адрес не указан"
        counts[address] = counts.get(address, 0) + 1
    return [{"address": address, "count": count} for address, count in sorted(counts.items())]


def _ready_at(item: ReturnModel) -> datetime:
    """От какого момента считать хранение: дата готовности WB, иначе — когда мы её увидели."""
    return item.ready_to_return_dt or item.status_changed_at
