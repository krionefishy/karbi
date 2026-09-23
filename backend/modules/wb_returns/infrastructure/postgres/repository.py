import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_returns.domain import CLAIM_OPEN, Claim, ReturnChange, ReturnItem
from backend.modules.wb_returns.infrastructure.postgres.models import (
    ClaimModel,
    NotificationLogModel,
    RefreshRequestModel,
    ReturnModel,
    TrackedSellerModel,
)

_CHUNK = 500


class ReturnsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- membership -------------------------------------------------------

    async def tracked_seller_ids(self) -> set[uuid.UUID]:
        return set(await self.session.scalars(select(TrackedSellerModel.seller_id)))

    async def tracked(self, seller_id: uuid.UUID) -> TrackedSellerModel | None:
        return await self.session.get(TrackedSellerModel, seller_id)

    async def track(self, seller_id: uuid.UUID) -> None:
        statement = insert(TrackedSellerModel).values(seller_id=seller_id)
        await self.session.execute(statement.on_conflict_do_nothing(index_elements=["seller_id"]))

    async def untrack(self, seller_id: uuid.UUID) -> None:
        await self.session.execute(delete(TrackedSellerModel).where(TrackedSellerModel.seller_id == seller_id))

    async def purge_seller(self, seller_id: uuid.UUID) -> None:
        for model in (ReturnModel, ClaimModel, NotificationLogModel, RefreshRequestModel):
            await self.session.execute(delete(model).where(model.seller_id == seller_id))
        await self.untrack(seller_id)

    async def still_tracked(self, seller_id: uuid.UUID) -> bool:
        """Подключён ли кабинет в момент записи, с блокировкой строки: отключение
        могло прийти, пока читался WB, и запись не должна воскресить стёртое."""
        found = await self.session.scalar(
            select(TrackedSellerModel.seller_id)
            .where(TrackedSellerModel.seller_id == seller_id)
            .with_for_update(read=True)
        )
        return found is not None

    # --- collection schedule ---------------------------------------------

    async def sellers_due(self, since: datetime, *, retry_after: datetime) -> list[uuid.UUID]:
        rows = await self.session.scalars(
            select(TrackedSellerModel.seller_id)
            .where(
                (TrackedSellerModel.collected_at.is_(None)) | (TrackedSellerModel.collected_at < since),
                (TrackedSellerModel.attempted_at.is_(None)) | (TrackedSellerModel.attempted_at < retry_after),
            )
            .order_by(TrackedSellerModel.enrolled_at)
        )
        return list(rows)

    async def record_attempt(self, seller_id: uuid.UUID) -> None:
        await self.session.execute(
            update(TrackedSellerModel)
            .where(TrackedSellerModel.seller_id == seller_id)
            .values(attempted_at=datetime.now(UTC))
        )

    async def finish_collection(self, seller_id: uuid.UUID, *, now: datetime) -> None:
        await self.session.execute(
            update(TrackedSellerModel)
            .where(TrackedSellerModel.seller_id == seller_id)
            .values(collected_at=now, collection_error=None)
        )

    async def mark_claims_archived(self, seller_id: uuid.UUID, *, now: datetime) -> None:
        await self.session.execute(
            update(TrackedSellerModel).where(TrackedSellerModel.seller_id == seller_id).values(claims_archived_at=now)
        )

    async def fail_collection(self, seller_id: uuid.UUID, error: str) -> None:
        """Прежние строки и их дата остаются; меняется только текст ошибки."""
        await self.session.execute(
            update(TrackedSellerModel)
            .where(TrackedSellerModel.seller_id == seller_id)
            .values(collection_error=error[:1000])
        )

    async def collection_summary(self, seller_ids: set[uuid.UUID]) -> tuple[datetime | None, int]:
        """Последний успех и число кабинетов с ошибкой."""
        if not seller_ids:
            return None, 0
        row = (
            await self.session.execute(
                select(
                    func.max(TrackedSellerModel.collected_at),
                    func.count().filter(TrackedSellerModel.collection_error.is_not(None)),
                ).where(TrackedSellerModel.seller_id.in_(seller_ids))
            )
        ).one()
        return row[0], int(row[1] or 0)

    # --- returns ----------------------------------------------------------

    async def upsert_returns(
        self, seller_id: uuid.UUID, items: Sequence[ReturnItem], *, now: datetime
    ) -> list[ReturnChange]:
        """Записать строки отчёта и вернуть те, у кого статус новый или изменился.

        Сравнение делается до записи: уведомлению нужен прежний статус, а не
        только текущий. `status_changed_at` сдвигается только у изменившихся.
        """
        if not items:
            return []
        # Один стикер дважды в одном отчёте — берём последнюю строку: ON CONFLICT
        # не умеет обновлять одну строку два раза за команду.
        items = list({item.shk_id: item for item in items}.values())
        keys = [item.shk_id for item in items]
        known: dict[int, str] = {}
        for offset in range(0, len(keys), _CHUNK):
            result = await self.session.execute(
                select(ReturnModel.shk_id, ReturnModel.status_key).where(
                    ReturnModel.seller_id == seller_id, ReturnModel.shk_id.in_(keys[offset : offset + _CHUNK])
                )
            )
            known.update({int(shk_id): status for shk_id, status in result})

        changes: list[ReturnChange] = []
        changed_ids: list[int] = []
        for item in items:
            previous = known.get(item.shk_id)
            if previous != item.status_key:
                changes.append(ReturnChange(item=item, previous_status_key=previous))
                changed_ids.append(item.shk_id)

        values = [self._return_values(seller_id, item, now) for item in items]
        for offset in range(0, len(values), _CHUNK):
            statement = insert(ReturnModel).values(values[offset : offset + _CHUNK])
            excluded = statement.excluded
            await self.session.execute(
                statement.on_conflict_do_update(
                    index_elements=["seller_id", "shk_id"],
                    set_={
                        column: getattr(excluded, column)
                        for column in (
                            "sticker_id",
                            "srid",
                            "order_id",
                            "nm_id",
                            "barcode",
                            "brand",
                            "subject_name",
                            "tech_size",
                            "return_type",
                            "reason",
                            "status",
                            "status_key",
                            "is_active",
                            "dst_office_id",
                            "dst_office_address",
                            "order_dt",
                            "ready_to_return_dt",
                            "expired_dt",
                            "completed_dt",
                            "collected_at",
                        )
                    },
                )
            )
        # Новые строки получили `status_changed_at = now` при вставке; известным
        # его сдвигаем отдельно, и только тем, у кого статус действительно другой.
        existing_changed = [shk_id for shk_id in changed_ids if shk_id in known]
        for offset in range(0, len(existing_changed), _CHUNK):
            await self.session.execute(
                update(ReturnModel)
                .where(
                    ReturnModel.seller_id == seller_id,
                    ReturnModel.shk_id.in_(existing_changed[offset : offset + _CHUNK]),
                )
                .values(status_changed_at=now)
            )
        return changes

    @staticmethod
    def _return_values(seller_id: uuid.UUID, item: ReturnItem, now: datetime) -> dict:
        return {
            "seller_id": seller_id,
            "shk_id": item.shk_id,
            "sticker_id": item.sticker_id[:32],
            "srid": item.srid[:64],
            "order_id": item.order_id,
            "nm_id": item.nm_id,
            "barcode": item.barcode[:64],
            "brand": item.brand[:255],
            "subject_name": item.subject_name[:255],
            "tech_size": item.tech_size[:32],
            "return_type": item.return_type[:255],
            "reason": item.reason[:512],
            "status": item.status[:64],
            "status_key": item.status_key,
            "is_active": item.is_active,
            "dst_office_id": item.dst_office_id,
            "dst_office_address": item.dst_office_address[:512],
            "order_dt": item.order_dt,
            "ready_to_return_dt": item.ready_to_return_dt,
            "expired_dt": item.expired_dt,
            "completed_dt": item.completed_dt,
            "first_seen_at": now,
            "status_changed_at": now,
            "collected_at": now,
        }

    async def active_returns(self, seller_id: uuid.UUID, *, status_key: str | None = None) -> list[ReturnModel]:
        """Возвраты в работе: свежие по статусу сверху, внутри — по адресу ПВЗ."""
        conditions = [ReturnModel.seller_id == seller_id, ReturnModel.is_active.is_(True)]
        if status_key:
            conditions.append(ReturnModel.status_key == status_key)
        rows = await self.session.scalars(
            select(ReturnModel)
            .where(*conditions)
            .order_by(ReturnModel.dst_office_address, ReturnModel.status_changed_at.desc(), ReturnModel.shk_id)
        )
        return list(rows)

    async def returns_history(self, seller_id: uuid.UUID, *, limit: int) -> list[ReturnModel]:
        rows = await self.session.scalars(
            select(ReturnModel)
            .where(ReturnModel.seller_id == seller_id, ReturnModel.is_active.is_(False))
            .order_by(ReturnModel.status_changed_at.desc(), ReturnModel.shk_id)
            .limit(limit)
        )
        return list(rows)

    # --- claims -----------------------------------------------------------

    async def upsert_claims(self, seller_id: uuid.UUID, claims: Sequence[Claim], *, now: datetime) -> list[Claim]:
        """Записать заявки и вернуть открытые, которых раньше не было."""
        if not claims:
            return []
        claims = list({claim.id: claim for claim in claims}.values())
        ids = [uuid.UUID(claim.id) for claim in claims]
        known: set[uuid.UUID] = set()
        for offset in range(0, len(ids), _CHUNK):
            known.update(
                await self.session.scalars(
                    select(ClaimModel.claim_id).where(
                        ClaimModel.seller_id == seller_id, ClaimModel.claim_id.in_(ids[offset : offset + _CHUNK])
                    )
                )
            )
        fresh = [claim for claim in claims if uuid.UUID(claim.id) not in known and claim.is_open]
        values = [
            {
                "seller_id": seller_id,
                "claim_id": uuid.UUID(claim.id),
                "claim_type": claim.claim_type,
                "status": claim.status,
                "status_ex": claim.status_ex,
                "nm_id": claim.nm_id,
                "imt_name": claim.imt_name[:512],
                "user_comment": claim.user_comment,
                "wb_comment": claim.wb_comment,
                "dt": claim.dt,
                "order_dt": claim.order_dt,
                "dt_update": claim.dt_update,
                "delivery_dt": claim.delivery_dt,
                "price": claim.price,
                "currency_code": claim.currency_code[:8],
                "srid": claim.srid[:64],
                "photos": list(claim.photos),
                "video_paths": list(claim.video_paths),
                "actions": list(claim.actions),
                "is_archive": claim.is_archive,
                "first_seen_at": now,
                "collected_at": now,
            }
            for claim in claims
        ]
        for offset in range(0, len(values), _CHUNK):
            statement = insert(ClaimModel).values(values[offset : offset + _CHUNK])
            excluded = statement.excluded
            await self.session.execute(
                statement.on_conflict_do_update(
                    index_elements=["seller_id", "claim_id"],
                    set_={
                        column: getattr(excluded, column)
                        for column in (
                            "status",
                            "status_ex",
                            "wb_comment",
                            "dt_update",
                            "delivery_dt",
                            "photos",
                            "video_paths",
                            "actions",
                            "is_archive",
                            "collected_at",
                        )
                    },
                )
            )
        return fresh

    async def close_missing_open_claims(self, seller_id: uuid.UUID, open_ids: Iterable[str], *, now: datetime) -> int:
        """Открытая заявка, которой больше нет среди открытых, решена и уехала в архив.

        Архив читается редко; без этого шага список открытых на странице отставал
        бы от кабинета на часы.
        """
        keep = [uuid.UUID(claim_id) for claim_id in open_ids]
        statement = (
            update(ClaimModel)
            .where(
                ClaimModel.seller_id == seller_id,
                ClaimModel.is_archive.is_(False),
                ClaimModel.status == CLAIM_OPEN,
            )
            .values(is_archive=True, collected_at=now)
        )
        if keep:
            statement = statement.where(ClaimModel.claim_id.not_in(keep))
        result = await self.session.execute(statement)
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def open_claims(self, seller_id: uuid.UUID) -> list[ClaimModel]:
        rows = await self.session.scalars(
            select(ClaimModel)
            .where(ClaimModel.seller_id == seller_id, ClaimModel.is_archive.is_(False), ClaimModel.status == CLAIM_OPEN)
            .order_by(ClaimModel.dt)
        )
        return list(rows)

    async def claims_history(self, seller_id: uuid.UUID, *, limit: int) -> list[ClaimModel]:
        rows = await self.session.scalars(
            select(ClaimModel)
            .where(ClaimModel.seller_id == seller_id, (ClaimModel.is_archive.is_(True)) | (ClaimModel.status != 0))
            .order_by(ClaimModel.dt.desc())
            .limit(limit)
        )
        return list(rows)

    # --- notification log --------------------------------------------------

    async def logged_keys(self, seller_id: uuid.UUID, kind: str, keys: Iterable[str]) -> set[str]:
        wanted = list(dict.fromkeys(keys))
        if not wanted:
            return set()
        found: set[str] = set()
        for offset in range(0, len(wanted), _CHUNK):
            found.update(
                await self.session.scalars(
                    select(NotificationLogModel.key).where(
                        NotificationLogModel.seller_id == seller_id,
                        NotificationLogModel.kind == kind,
                        NotificationLogModel.key.in_(wanted[offset : offset + _CHUNK]),
                    )
                )
            )
        return found

    async def log_sent(self, seller_id: uuid.UUID, kind: str, keys: Iterable[str], *, now: datetime) -> None:
        values = [
            {"seller_id": seller_id, "kind": kind, "key": key[:128], "sent_at": now} for key in dict.fromkeys(keys)
        ]
        for offset in range(0, len(values), _CHUNK):
            statement = insert(NotificationLogModel).values(values[offset : offset + _CHUNK])
            await self.session.execute(statement.on_conflict_do_nothing(index_elements=["seller_id", "kind", "key"]))

    # --- manual refresh --------------------------------------------------

    async def request_refresh(self, seller_id: uuid.UUID, requested_by: uuid.UUID | None) -> RefreshRequestModel:
        """Поставить запрос или вернуть тот, что уже ждёт: уникальный индекс
        останавливает второе одновременное нажатие, и оно получает первый."""
        pending = await self.active_refresh(seller_id)
        if pending is not None:
            return pending
        request = RefreshRequestModel(seller_id=seller_id, requested_by=requested_by)
        try:
            async with self.session.begin_nested():
                self.session.add(request)
                await self.session.flush()
        except IntegrityError:
            raced = await self.active_refresh(seller_id)
            if raced is None:
                raise
            return raced
        return request

    async def active_refresh(self, seller_id: uuid.UUID) -> RefreshRequestModel | None:
        return await self.session.scalar(
            select(RefreshRequestModel)
            .where(
                RefreshRequestModel.seller_id == seller_id,
                RefreshRequestModel.status.in_(("queued", "running")),
            )
            .order_by(RefreshRequestModel.requested_at)
            .limit(1)
        )

    async def latest_refresh(self, seller_id: uuid.UUID) -> RefreshRequestModel | None:
        return await self.session.scalar(
            select(RefreshRequestModel)
            .where(RefreshRequestModel.seller_id == seller_id)
            .order_by(RefreshRequestModel.requested_at.desc())
            .limit(1)
        )

    async def claim_refreshes(self, limit: int = 5) -> list[RefreshRequestModel]:
        requests = list(
            await self.session.scalars(
                select(RefreshRequestModel)
                .where(RefreshRequestModel.status == "queued")
                .order_by(RefreshRequestModel.requested_at)
                .with_for_update(skip_locked=True)
                .limit(limit)
            )
        )
        for request in requests:
            request.status = "running"
            request.started_at = datetime.now(UTC)
        return requests

    async def abandon_stale_refreshes(self, started_before: datetime) -> int:
        result = await self.session.execute(
            update(RefreshRequestModel)
            .where(RefreshRequestModel.status == "running", RefreshRequestModel.started_at < started_before)
            .values(status="error", error="Сбор прервался: воркер перезапустился", finished_at=datetime.now(UTC))
            .returning(RefreshRequestModel.id)
        )
        return len(result.all())

    async def finish_refresh(self, request_id: uuid.UUID, error: str | None = None) -> None:
        await self.session.execute(
            update(RefreshRequestModel)
            .where(RefreshRequestModel.id == request_id)
            .values(
                status="error" if error else "success",
                error=error[:1000] if error else None,
                finished_at=datetime.now(UTC),
            )
        )

    @staticmethod
    def to_item(model: ReturnModel) -> ReturnItem:
        return ReturnItem(
            shk_id=model.shk_id,
            sticker_id=model.sticker_id,
            srid=model.srid,
            order_id=model.order_id,
            nm_id=model.nm_id,
            barcode=model.barcode,
            brand=model.brand,
            subject_name=model.subject_name,
            tech_size=model.tech_size,
            return_type=model.return_type,
            reason=model.reason,
            status=model.status,
            is_active=model.is_active,
            dst_office_id=model.dst_office_id,
            dst_office_address=model.dst_office_address,
            order_dt=model.order_dt,
            ready_to_return_dt=model.ready_to_return_dt,
            expired_dt=model.expired_dt,
            completed_dt=model.completed_dt,
        )

    @staticmethod
    def to_claim(model: ClaimModel) -> Claim:
        return Claim(
            id=str(model.claim_id),
            claim_type=model.claim_type,
            status=model.status,
            status_ex=model.status_ex,
            nm_id=model.nm_id,
            imt_name=model.imt_name,
            user_comment=model.user_comment,
            wb_comment=model.wb_comment,
            dt=model.dt,
            order_dt=model.order_dt,
            dt_update=model.dt_update,
            delivery_dt=model.delivery_dt,
            price=float(model.price or 0),
            currency_code=model.currency_code,
            srid=model.srid,
            photos=tuple(model.photos or ()),
            video_paths=tuple(model.video_paths or ()),
            actions=tuple(model.actions or ()),
            is_archive=model.is_archive,
        )
