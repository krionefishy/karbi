"""Расширение в браузере владельца: пара, коды дня, heartbeat и задачи.

Сервер не может достучаться до браузера, поэтому всё идёт от расширения:
оно обменивает код пары на токен, присылает коды на несколько дней вперёд,
раз в десять минут сообщает, что живо, и забирает задачи «обнови код».
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.application import SellerNotFoundError
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_returns.application import qr as qr_tools
from backend.modules.wb_returns.application.outbox import publish_chat_message
from backend.modules.wb_returns.infrastructure.postgres import (
    DeliveryCodeModel,
    ExtensionInstallModel,
    PairingCodeModel,
    ReturnsRepository,
)

TASK_REFRESH_CODE = "refresh_code"
TEMPLATE_CODE = "returns.code"
STATE_OK = "ok"
STATE_NEEDS_LOGIN = "needs_login"
STATE_ERROR = "error"
STATES = (STATE_OK, STATE_NEEDS_LOGIN, STATE_ERROR)
TOKEN_PREFIX = "mar_"
# Задачу, которую расширение забрало, но за четверть часа не выполнило, отдаём снова.
RETAKE_AFTER = timedelta(minutes=15)


class PairingCodeInvalidError(Exception):
    """Код не найден, уже использован или просрочен."""


class ExtensionUnauthorizedError(Exception):
    """Токена нет, он отозван или не наш."""


@dataclass(frozen=True, slots=True)
class PairResult:
    token: str
    install_id: uuid.UUID
    seller_id: uuid.UUID
    seller_name: str


@dataclass(frozen=True, slots=True)
class HeartbeatResult:
    seller_name: str
    has_code_today: bool
    refresh_code: bool


@dataclass(frozen=True, slots=True)
class IngestResult:
    accepted: int
    replied_chats: list[int] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class InstallView:
    id: uuid.UUID
    install_id: str
    browser: str
    created_at: datetime
    last_seen_at: datetime | None
    state: str
    last_error: str | None
    last_code_at: datetime | None
    deliveries_count: int
    deliveries_at: datetime | None


@dataclass(frozen=True, slots=True)
class ExtensionView:
    seller_id: uuid.UUID
    download_url: str
    installs: tuple[InstallView, ...]
    code_date: date
    has_code_today: bool
    code_received_at: datetime | None
    pending_tasks: bool


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class ExtensionService:
    def __init__(
        self,
        session: AsyncSession,
        sellers: SellerRepository,
        returns: ReturnsRepository,
        *,
        bot_code: str,
        timezone: ZoneInfo,
        public_base_url: str,
        download_path: str,
        pairing_ttl_minutes: int,
        qr_secret: str,
    ) -> None:
        self.session = session
        self.sellers = sellers
        self.returns = returns
        self.bot_code = bot_code
        self.timezone = timezone
        self.public_base_url = public_base_url.rstrip("/")
        self.download_path = download_path
        self.pairing_ttl = timedelta(minutes=pairing_ttl_minutes)
        self.qr_secret = qr_secret

    # --- links ------------------------------------------------------------

    @property
    def download_url(self) -> str:
        return f"{self.public_base_url}{self.download_path}"

    def qr_url(self, seller_id: uuid.UUID, day: date) -> str:
        sig = qr_tools.signature(self.qr_secret, seller_id, day)
        return f"{self.public_base_url}/api/v1/wb/returns/qr/{seller_id}/{day.isoformat()}/{sig}.png"

    def verify_qr(self, seller_id: uuid.UUID, day: date, candidate: str) -> bool:
        return qr_tools.verify(self.qr_secret, seller_id, day, candidate)

    def today(self, now: datetime) -> date:
        return now.astimezone(self.timezone).date()

    # --- pairing ----------------------------------------------------------

    async def create_pairing_code(
        self,
        seller_id: uuid.UUID,
        *,
        chat_id: int | None = None,
        created_by: uuid.UUID | None = None,
        now: datetime | None = None,
    ) -> PairingCodeModel:
        stamp = now or datetime.now(UTC)
        await self._enrolled(seller_id)
        await self.returns.expire_pairing_codes(seller_id, chat_id=chat_id, now=stamp)
        for _ in range(10):
            code = f"{secrets.randbelow(1_000_000):06d}"
            try:
                async with self.session.begin_nested():
                    return await self.returns.add_pairing_code(
                        seller_id=seller_id,
                        code=code,
                        chat_id=chat_id,
                        created_by=created_by,
                        expires_at=stamp + self.pairing_ttl,
                    )
            except IntegrityError:
                continue
        raise RuntimeError("could not allocate a pairing code")

    async def pair(self, code: str, *, install_id: str, browser: str, now: datetime | None = None) -> PairResult:
        stamp = now or datetime.now(UTC)
        pairing = await self.returns.live_pairing_code(code.strip(), now=stamp)
        if pairing is None:
            raise PairingCodeInvalidError
        seller = await self.sellers.get(pairing.seller_id)
        if seller is None or seller.archived_at is not None:
            raise PairingCodeInvalidError
        await self.returns.revoke_installs_of_same_browser(pairing.seller_id, install_id, now=stamp)
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        install = await self.returns.add_install(
            seller_id=pairing.seller_id,
            install_id=install_id,
            browser=browser,
            token_hash=hash_token(token),
            chat_id=pairing.chat_id,
        )
        pairing.used_at = stamp
        pairing.install_id = install.id
        await self.session.commit()
        return PairResult(token=token, install_id=install.id, seller_id=pairing.seller_id, seller_name=seller.name)

    async def authenticate(self, token: str, *, now: datetime | None = None) -> ExtensionInstallModel:
        if not token.startswith(TOKEN_PREFIX):
            raise ExtensionUnauthorizedError
        install = await self.returns.install_by_token_hash(hash_token(token))
        if install is None:
            raise ExtensionUnauthorizedError
        install.last_seen_at = now or datetime.now(UTC)
        return install

    # --- data from the extension ---------------------------------------

    async def ingest_codes(
        self, install: ExtensionInstallModel, rows: list[dict[str, Any]], *, now: datetime | None = None
    ) -> IngestResult:
        stamp = now or datetime.now(UTC)
        parsed = []
        for row in rows:
            try:
                day = date.fromisoformat(str(row.get("date") or "")[:10])
            except ValueError:
                continue
            parsed.append({**row, "code_date": day})
        accepted = await self.returns.upsert_delivery_codes(install.seller_id, parsed, install_id=install.id, now=stamp)
        today = self.today(stamp)
        replied: list[int] = []
        if any(row["code_date"] == today for row in parsed):
            install.last_code_at = stamp
            install.state = STATE_OK
            install.last_error = None
            seller = await self.sellers.get(install.seller_id)
            seller_name = seller.name if seller else "магазин"
            for task in await self.returns.fulfil_tasks(install.seller_id, kind=TASK_REFRESH_CODE, now=stamp):
                if task.chat_id is None:
                    continue
                publish_chat_message(
                    self.session,
                    bot_code=self.bot_code,
                    chat_id=task.chat_id,
                    template=TEMPLATE_CODE,
                    params={"codes": [await self.code_params(install.seller_id, seller_name, today)]},
                    dedupe_key=f"returns:code-task:{task.id}",
                )
                replied.append(task.chat_id)
        await self.session.commit()
        return IngestResult(accepted=accepted, replied_chats=replied)

    async def ingest_deliveries(
        self, install: ExtensionInstallModel, items: list[dict[str, Any]], *, now: datetime | None = None
    ) -> int:
        install.deliveries = items[:200]
        install.deliveries_at = now or datetime.now(UTC)
        await self.session.commit()
        return len(install.deliveries)

    async def heartbeat(
        self, install: ExtensionInstallModel, *, state: str, error: str | None, now: datetime | None = None
    ) -> HeartbeatResult:
        stamp = now or datetime.now(UTC)
        install.state = state if state in STATES else STATE_ERROR
        install.last_error = (error or None) and str(error)[:1000]
        seller = await self.sellers.get(install.seller_id)
        tasks = await self.returns.take_tasks(install.seller_id, now=stamp, retake_after=RETAKE_AFTER)
        has_code = await self.returns.delivery_code(install.seller_id, self.today(stamp)) is not None
        await self.session.commit()
        return HeartbeatResult(
            seller_name=seller.name if seller else "",
            has_code_today=has_code,
            refresh_code=any(task.kind == TASK_REFRESH_CODE for task in tasks),
        )

    # --- for the bot and the page ------------------------------------------

    async def code_params(self, seller_id: uuid.UUID, seller_name: str, day: date) -> dict[str, Any]:
        """Код на день в виде параметров шаблона; без кода — почему его нет."""
        stored = await self.returns.delivery_code(seller_id, day)
        params: dict[str, Any] = {"name": seller_name, "date": day.isoformat(), "code": None}
        if stored is not None and (stored.code or stored.qr):
            params.update(self._code_fields(stored, seller_id, day))
            return params
        installs = await self.returns.active_installs(seller_id)
        params["no_install"] = not installs
        return params

    def _code_fields(self, stored: DeliveryCodeModel, seller_id: uuid.UUID, day: date) -> dict[str, Any]:
        return {
            "code": stored.code or stored.ext_code or None,
            "ext_code": stored.ext_code or None,
            "qr": stored.qr or stored.ext_qr or None,
            "qr_url": self.qr_url(seller_id, day) if (stored.qr or stored.ext_qr) else None,
        }

    async def request_code(
        self, seller_id: uuid.UUID, seller_name: str, *, chat_id: int, now: datetime
    ) -> dict[str, Any]:
        """Код на сегодня, а если его нет — задача расширению; ответ придёт, когда код приедет."""
        params = await self.code_params(seller_id, seller_name, self.today(now))
        if params.get("code") or params.get("no_install"):
            return params
        if not await self.returns.has_open_task(seller_id, kind=TASK_REFRESH_CODE):
            await self.returns.add_task(seller_id, kind=TASK_REFRESH_CODE, chat_id=chat_id)
        params["requested"] = True
        return params

    async def view(self, seller_id: uuid.UUID, *, now: datetime | None = None) -> ExtensionView:
        stamp = now or datetime.now(UTC)
        await self._enrolled(seller_id)
        today = self.today(stamp)
        stored = await self.returns.delivery_code(seller_id, today)
        installs = await self.returns.active_installs(seller_id)
        return ExtensionView(
            seller_id=seller_id,
            download_url=self.download_url,
            installs=tuple(
                InstallView(
                    id=item.id,
                    install_id=item.install_id,
                    browser=item.browser,
                    created_at=item.created_at,
                    last_seen_at=item.last_seen_at,
                    state=item.state,
                    last_error=item.last_error,
                    last_code_at=item.last_code_at,
                    deliveries_count=len(item.deliveries or []),
                    deliveries_at=item.deliveries_at,
                )
                for item in installs
            ),
            code_date=today,
            has_code_today=stored is not None,
            code_received_at=stored.received_at if stored else None,
            pending_tasks=await self.returns.has_open_task(seller_id, kind=TASK_REFRESH_CODE),
        )

    async def revoke(self, seller_id: uuid.UUID, install_id: uuid.UUID, *, now: datetime | None = None) -> bool:
        await self._enrolled(seller_id)
        revoked = await self.returns.revoke_install(seller_id, install_id, now=now or datetime.now(UTC))
        await self.session.commit()
        return revoked

    async def qr_png(self, seller_id: uuid.UUID, day: date) -> bytes | None:
        stored = await self.returns.delivery_code(seller_id, day)
        text = (stored.qr or stored.ext_qr) if stored else ""
        return qr_tools.render_png(text) if text else None

    async def _enrolled(self, seller_id: uuid.UUID) -> str:
        seller = await self.sellers.get(seller_id)
        if seller is None or seller.archived_at is not None or await self.returns.tracked(seller_id) is None:
            raise SellerNotFoundError
        return seller.name
