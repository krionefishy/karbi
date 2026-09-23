"""Заявки покупателей на возврат: `GET /api/v1/claims` на returns-api.

Открытые заявки — только за последние 14 дней и страницами по 200; лимит —
20 запросов в минуту на кабинет. Архив за 3 месяца может быть на сотни
заявок, поэтому его читают реже открытых.
"""

import uuid
from typing import Any

from backend.modules.wb_core.infrastructure.wb import WBJsonClient, WBPermanentError
from backend.modules.wb_returns.domain import Claim
from backend.modules.wb_returns.infrastructure.wb.parsing import as_float, as_int, as_moment, as_text

RETURNS_BUCKET = "returns"
CLAIMS_PATH = "/api/v1/claims"
PAGE_LIMIT = 200
# Предохранитель от бесконечной пагинации, если WB перестанет отдавать `total`.
MAX_PAGES = 50


class WBClaimsClient(WBJsonClient):
    bucket = RETURNS_BUCKET
    api_name = "WB Returns API"
    category = "Возвраты покупателями"

    async def claims(self, seller_id: str, *, archive: bool) -> list[Claim]:
        collected: list[Claim] = []
        offset = 0
        for _ in range(MAX_PAGES):
            payload = await self.request(
                "GET",
                CLAIMS_PATH,
                seller_id,
                params={"is_archive": "true" if archive else "false", "limit": PAGE_LIMIT, "offset": offset},
            )
            rows = payload.get("claims") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                raise WBPermanentError(f"{self.api_name}: список заявок пришёл без поля claims")
            page = [item for item in (self._claim(row, archive) for row in rows if isinstance(row, dict)) if item]
            collected.extend(page)
            total = as_int(payload.get("total"))
            offset += len(rows)
            if not rows or len(rows) < PAGE_LIMIT or (total and offset >= total):
                break
        return collected

    @staticmethod
    def _claim(row: dict[str, Any], archive: bool) -> Claim | None:
        raw_id = as_text(row.get("id"))
        try:
            claim_id = uuid.UUID(raw_id)
        except ValueError:
            return None
        moment = as_moment(row.get("dt"))
        if moment is None:
            return None
        return Claim(
            id=str(claim_id),
            claim_type=as_int(row.get("claim_type")),
            status=as_int(row.get("status")),
            status_ex=as_int(row.get("status_ex")),
            nm_id=as_int(row.get("nm_id")),
            imt_name=as_text(row.get("imt_name")),
            user_comment=as_text(row.get("user_comment")),
            wb_comment=as_text(row.get("wb_comment")),
            dt=moment,
            order_dt=as_moment(row.get("order_dt")),
            dt_update=as_moment(row.get("dt_update")),
            delivery_dt=as_moment(row.get("delivery_dt")),
            price=as_float(row.get("price")),
            currency_code=as_text(row.get("currency_code")),
            srid=as_text(row.get("srid")),
            photos=_strings(row.get("photos")),
            video_paths=_strings(row.get("video_paths")),
            actions=_strings(row.get("actions")),
            is_archive=archive,
        )


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str) and item)
