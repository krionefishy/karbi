"""Ответы бота возвратов на команды из чата.

Команда приходит событием из модуля уведомлений вместе с селлерами чата;
ответ уходит обычным событием сообщения с аудиторией «чат». Ключ
дедупликации — id события: повтор из Kafka не отвечает дважды.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.notifications.domain import COMMAND_START, CommandEvent
from backend.modules.wb_returns.application.extension import ExtensionService
from backend.modules.wb_returns.application.outbox import publish_chat_message
from backend.modules.wb_returns.domain import FREE_STORAGE_DAYS, RETURN_READY, RETURN_TRANSIT, STORAGE_DAYS
from backend.modules.wb_returns.infrastructure.postgres import ReturnModel, ReturnsRepository

TEMPLATE_WELCOME = "returns.welcome"
TEMPLATE_HELP = "returns.help"
TEMPLATE_LIST = "returns.list"
TEMPLATE_CODE = "returns.code"
TEMPLATE_EXTENSION = "returns.extension"
TEMPLATE_NO_SUBSCRIPTION = "returns.no_subscription"
TEMPLATE_UNKNOWN = "returns.unknown"

COMMANDS = ("help", "returns", "qr", "extension")
ITEMS_LIMIT = 40


class CommandService:
    def __init__(
        self,
        session: AsyncSession,
        returns: ReturnsRepository,
        extension: ExtensionService,
        *,
        bot_code: str,
        timezone: ZoneInfo,
    ) -> None:
        self.session = session
        self.returns = returns
        self.extension = extension
        self.bot_code = bot_code
        self.timezone = timezone
        self.logger = logging.getLogger("wb.returns.commands")

    async def handle(self, event: CommandEvent, *, now: datetime | None = None) -> str | None:
        """Ответить на одну команду. Возвращает шаблон ответа, None — команда не наша."""
        if event.bot_code != self.bot_code:
            return None
        stamp = now or datetime.now(UTC)
        command = event.command
        params: dict[str, Any]
        if command == COMMAND_START:
            template, params = TEMPLATE_WELCOME, {"sellers": [name for _, name in event.sellers]}
        elif command == "help":
            template, params = TEMPLATE_HELP, {}
        elif not event.sellers:
            template, params = TEMPLATE_NO_SUBSCRIPTION, {}
        elif command == "returns":
            template, params = TEMPLATE_LIST, {"sellers": await self._sellers_summary(event, stamp)}
        elif command == "qr":
            # Картинка QR — одна на сообщение, поэтому по сообщению на магазин.
            for seller_id, name in event.sellers:
                params = await self.extension.request_code(seller_id, name, chat_id=event.chat_id, now=stamp)
                self._reply(event, TEMPLATE_CODE, params, suffix=str(seller_id))
            await self.session.commit()
            return TEMPLATE_CODE
        elif command == "extension":
            template, params = TEMPLATE_EXTENSION, await self._pairing(event, stamp)
        else:
            template, params = TEMPLATE_UNKNOWN, {"command": f"/{command}"}
        self._reply(event, template, params)
        await self.session.commit()
        return template

    async def _sellers_summary(self, event: CommandEvent, now: datetime) -> list[dict]:
        summary = []
        for seller_id, seller_name in event.sellers:
            active = await self.returns.active_returns(seller_id)
            ready = [item for item in active if item.status_key == RETURN_READY]
            transit = [item for item in active if item.status_key == RETURN_TRANSIT]
            summary.append(
                {
                    "name": seller_name,
                    "ready_total": len(ready),
                    "transit_total": len(transit),
                    "other_total": len(active) - len(ready) - len(transit),
                    "offices": self._offices(ready),
                    "open_claims": len(await self.returns.open_claims(seller_id)),
                }
            )
        return summary

    def _offices(self, items: list[ReturnModel]) -> list[dict]:
        grouped: dict[str, list[ReturnModel]] = {}
        for item in items:
            grouped.setdefault(item.dst_office_address or "ПВЗ не указан", []).append(item)
        offices = []
        for address, rows in sorted(grouped.items()):
            ready_at = min((row.ready_to_return_dt or row.status_changed_at) for row in rows)
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

    async def _pairing(self, event: CommandEvent, now: datetime) -> dict:
        codes = []
        for seller_id, name in event.sellers:
            pairing = await self.extension.create_pairing_code(seller_id, chat_id=event.chat_id, now=now)
            codes.append(
                {
                    "name": name,
                    "code": pairing.code,
                    "expires_at": pairing.expires_at.astimezone(self.timezone).isoformat(),
                }
            )
        return {
            "download_url": self.extension.download_url,
            "ttl_minutes": int(self.extension.pairing_ttl.total_seconds() // 60),
            "codes": codes,
        }

    def _reply(self, event: CommandEvent, template: str, params: dict, *, suffix: str = "") -> None:
        publish_chat_message(
            self.session,
            bot_code=self.bot_code,
            chat_id=event.chat_id,
            template=template,
            params=params,
            dedupe_key=f"returns:reply:{event.event_id}" + (f":{suffix}" if suffix else ""),
            event_type="ReturnsCommandReplied",
        )
