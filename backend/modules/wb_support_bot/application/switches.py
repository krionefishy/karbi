"""Тумблеры рассылки: диалоги по товарам, тумблер товара и ручное переключение диалога."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.application import CatalogMirror
from backend.modules.wb_support_bot.application.mailing import mailing_for
from backend.modules.wb_support_bot.domain import (
    MANUAL_ALLOW,
    MANUAL_DENY,
    REASON_NO_CONTACT,
    REASON_PRODUCT_OFF,
    REASON_REPLY_HOLD,
    REASON_STATE,
    SWITCHABLE_STATES,
    Mailing,
)
from backend.modules.wb_support_bot.infrastructure.postgres import (
    NO_ARTICLE_KEY,
    DialogKey,
    DialogModel,
    SupportBotRepository,
    VerdictModel,
)

SCOPE_CAMPAIGN = "campaign"
SCOPE_ALL = "all"
SCOPES = (SCOPE_CAMPAIGN, SCOPE_ALL)

LOCK_TEXT = {
    REASON_STATE: "по этому диалогу рассылки нет: он не идёт и не ждёт начала",
    REASON_NO_CONTACT: "покупатель просил не писать",
    REASON_PRODUCT_OFF: "товар выключен, включите его целиком",
    REASON_REPLY_HOLD: "ответ покупателя ещё оценивается",
}


class SwitchError(Exception):
    """Тумблер нельзя переключить: диалог не найден, заблокирован или запрос неверный."""


@dataclass(frozen=True, slots=True)
class ProductGroup:
    """Товар со счётчиками диалогов: `writing` — по ним бот пишет сейчас, `waiting` — по ним не пишет."""

    key: int
    nm_id: int | None
    name: str
    vendor_code: str
    photo_url: str
    removed: bool
    enabled: bool
    total: int
    writing: int
    waiting: int


@dataclass(frozen=True, slots=True)
class SwitchDialog:
    model: DialogModel
    mailing: Mailing
    verdict: VerdictModel | None


@dataclass(frozen=True, slots=True)
class ProductDialogs:
    dialogs: tuple[SwitchDialog, ...]
    total: int
    page: int
    page_size: int


def _key(dialog: DialogModel) -> int:
    return dialog.nm_id if dialog.nm_id is not None else NO_ARTICLE_KEY


def _scope_states(scope: str) -> tuple[str, ...] | None:
    if scope not in SCOPES:
        raise SwitchError(f"Неизвестный набор диалогов: {scope}")
    return SWITCHABLE_STATES if scope == SCOPE_CAMPAIGN else None


class SwitchesMixin:
    """Страница тумблеров: решение «пишем или нет» считает та же функция, что и при отправке."""

    repository: SupportBotRepository
    session: AsyncSession
    classifier_mode: str
    classifier_prompt_version: str
    manual_switch_hours: int
    history: timedelta

    async def _tracked(self, seller_id: uuid.UUID):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def _decide(self, dialog: DialogModel, verdicts: dict[tuple[str, str], VerdictModel], enabled: bool, now: datetime):
        row = verdicts.get((dialog.chat_id, dialog.tail_fingerprint)) if dialog.tail_fingerprint else None
        return row, mailing_for(dialog, row, mode=self.classifier_mode, product_enabled=enabled, now=now)

    async def products(self, seller_id: uuid.UUID, *, scope: str, now: datetime | None = None) -> list[ProductGroup]:
        """Товары кабинета: сначала включённые (где пишем больше, выше), выключенные в самом конце."""
        await self._tracked(seller_id)
        moment = now or datetime.now(UTC)
        dialogs = await self.repository.dialogs_for_switches(
            seller_id, states=_scope_states(scope), since=moment - self.history
        )
        disabled = await self.repository.disabled_products(seller_id)
        verdicts = await self.repository.verdicts_by_tail(seller_id, self.classifier_prompt_version)
        total: dict[int, int] = {}
        writing: dict[int, int] = {}
        for dialog in dialogs:
            key = _key(dialog)
            _, mailing = self._decide(dialog, verdicts, key not in disabled, moment)
            total[key] = total.get(key, 0) + 1
            writing[key] = writing.get(key, 0) + (1 if mailing.on else 0)
        keys = set(total) | disabled
        cards = await CatalogMirror(self.session).cards(seller_id, [str(key) for key in keys if key != NO_ARTICLE_KEY])
        groups: list[ProductGroup] = []
        for key in keys:
            card = cards.get(str(key))
            nm_id = key if key != NO_ARTICLE_KEY else None
            groups.append(
                ProductGroup(
                    key=key,
                    nm_id=nm_id,
                    name=card.name if card and card.name else ("Без артикула" if nm_id is None else f"Артикул {nm_id}"),
                    vendor_code=card.vendor_code if card else "",
                    photo_url=card.photo_url if card else "",
                    removed=bool(card and card.state != "active"),
                    enabled=key not in disabled,
                    total=total.get(key, 0),
                    writing=writing.get(key, 0),
                    waiting=total.get(key, 0) - writing.get(key, 0),
                )
            )
        groups.sort(key=lambda group: (not group.enabled, -group.writing, -group.total, group.key))
        return groups

    async def product_dialogs(
        self, seller_id: uuid.UUID, key: int, *, scope: str, page: int, page_size: int, now: datetime | None = None
    ) -> ProductDialogs:
        await self._tracked(seller_id)
        if key < 0:
            raise SwitchError("Неверный ключ товара")
        moment = now or datetime.now(UTC)
        dialogs = await self.repository.dialogs_for_switches(
            seller_id, states=_scope_states(scope), since=moment - self.history, nm_key=key
        )
        enabled = key not in await self.repository.disabled_products(seller_id)
        verdicts = await self.repository.verdicts_by_tail(seller_id, self.classifier_prompt_version)
        rows: list[SwitchDialog] = []
        for dialog in dialogs:
            row, mailing = self._decide(dialog, verdicts, enabled, moment)
            rows.append(SwitchDialog(dialog, mailing, row if row is not None and row.status == "done" else None))
        # Те, по кому пишем, выше; дальше по свежести.
        rows.sort(key=lambda item: (not item.mailing.on, -item.model.updated_at.timestamp()))
        start = (page - 1) * page_size
        return ProductDialogs(tuple(rows[start : start + page_size]), len(rows), page, page_size)

    async def set_product_enabled(
        self,
        seller_id: uuid.UUID,
        key: int,
        enabled: bool,
        *,
        by: uuid.UUID | None = None,
        now: datetime | None = None,
    ) -> None:
        await self._tracked(seller_id)
        if key < 0:
            raise SwitchError("Неверный ключ товара")
        await self.repository.set_product_enabled(seller_id, key, enabled, by=by, now=now)
        await self.session.commit()

    async def set_dialog_switch(
        self, seller_id: uuid.UUID, chat_id: str, prompt_at: datetime, on: bool | None, *, now: datetime | None = None
    ) -> None:
        """Включает или выключает рассылку по диалогу; `on=None` возвращает тумблер к решению модели.

        Ручная отметка ставится, только если расходится с решением модели, и действует
        `manual_switch_hours` часов. Исключённый диалог включается обратно без срока.
        """
        await self._tracked(seller_id)
        moment = now or datetime.now(UTC)
        key = DialogKey(seller_id, chat_id, prompt_at)
        dialog = await self.repository.get_dialog(key)
        if dialog is None:
            raise SwitchError("Диалог не найден")
        disabled = await self.repository.disabled_products(seller_id)
        verdicts = await self.repository.verdicts_by_tail(seller_id, self.classifier_prompt_version)
        enabled = _key(dialog) not in disabled
        _, current = self._decide(dialog, verdicts, enabled, moment)
        if current.locked:
            raise SwitchError(f"Тумблер недоступен: {LOCK_TEXT.get(current.reason, 'диалог заблокирован')}")
        if on is None:
            await self.repository.set_manual_verdict(key, None)
        else:
            if on and dialog.excluded:
                dialog.excluded = False
                dialog.next_send_at = None
            _, base = self._decide(_without_manual(dialog), verdicts, enabled, moment)
            if base.on == on:
                await self.repository.set_manual_verdict(key, None)
            else:
                until = moment + timedelta(hours=self.manual_switch_hours)
                await self.repository.set_manual_verdict(key, MANUAL_ALLOW if on else MANUAL_DENY, until=until)
        await self.session.commit()


def _without_manual(dialog: DialogModel) -> DialogModel:
    """Копия диалога без ручной отметки: нужна, чтобы узнать, что сказала бы модель без неё."""
    clone = DialogModel(
        seller_id=dialog.seller_id,
        chat_id=dialog.chat_id,
        prompt_at=dialog.prompt_at,
        state=dialog.state,
        nm_id=dialog.nm_id,
        no_contact=dialog.no_contact,
        excluded=dialog.excluded,
        closed=dialog.closed,
        reply_hold=dialog.reply_hold,
        tail_fingerprint=dialog.tail_fingerprint,
    )
    return clone


__all__ = [
    "LOCK_TEXT",
    "SCOPES",
    "SCOPE_ALL",
    "SCOPE_CAMPAIGN",
    "ProductDialogs",
    "ProductGroup",
    "SwitchDialog",
    "SwitchError",
    "SwitchesMixin",
]
