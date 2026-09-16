import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime

from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from backend.modules.wb_fbs_penalties.domain import (
    GROUP_DEDUCTIONS,
    GROUP_LOGISTICS,
    GROUP_PENALTIES,
    GROUP_STORAGE,
    ReportHeader,
    ReportRow,
)
from backend.modules.wb_fbs_penalties.infrastructure.postgres.models import (
    RefreshRequestModel,
    ReportModel,
    ReportRowModel,
    TrackedSellerModel,
)

_CHUNK = 500


class PenaltiesRepository:
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
        for model in (ReportRowModel, ReportModel, RefreshRequestModel):
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

    # --- reports ----------------------------------------------------------

    async def upsert_reports(self, seller_id: uuid.UUID, headers: Iterable[ReportHeader], *, now: datetime) -> int:
        """Список отчётов: новые появляются, известные обновляют суммы; курсор и отметка загрузки не трогаются."""
        rows = [
            {
                "seller_id": seller_id,
                "report_id": header.report_id,
                "date_from": header.date_from,
                "date_to": header.date_to,
                "create_date": header.create_date,
                "report_type": header.report_type,
                "penalty_sum": header.penalty_sum,
                "deduction_sum": header.deduction_sum,
                "seen_at": now,
            }
            for header in headers
        ]
        if not rows:
            return 0
        statement = insert(ReportModel).values(rows)
        excluded = statement.excluded
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["seller_id", "report_id"],
                set_={
                    "date_from": excluded.date_from,
                    "date_to": excluded.date_to,
                    "create_date": excluded.create_date,
                    "penalty_sum": excluded.penalty_sum,
                    "deduction_sum": excluded.deduction_sum,
                    "seen_at": excluded.seen_at,
                },
            )
        )
        return len(rows)

    async def finish_covered_reports(self, seller_id: uuid.UUID, *, now: datetime) -> None:
        """Отметить прочитанными отчёты, чей период целиком лежит в уже прочитанном.

        Суточный отчёт — те же строки, что в недельном за ту же неделю (`rrd_id`
        совпадает): кабинет, собранный по недельным отчётам, не перечитывает три
        месяца суточных, а берёт только дни после последней недели.
        """
        covering = aliased(ReportModel)
        covered = (
            select(ReportModel.report_id)
            .join(
                covering,
                (covering.seller_id == ReportModel.seller_id)
                & (covering.report_id != ReportModel.report_id)
                & covering.loaded_at.is_not(None)
                & (covering.date_from <= ReportModel.date_from)
                & (covering.date_to >= ReportModel.date_to)
                # Строго шире: два отчёта за один и тот же период друг друга не заменяют.
                & ((covering.date_to - covering.date_from) > (ReportModel.date_to - ReportModel.date_from)),
            )
            .where(ReportModel.seller_id == seller_id, ReportModel.loaded_at.is_(None))
        )
        await self.session.execute(
            update(ReportModel)
            .where(ReportModel.seller_id == seller_id, ReportModel.report_id.in_(covered))
            .values(loaded_at=now)
        )

    async def pending_reports(self, seller_id: uuid.UUID, *, limit: int) -> list[ReportModel]:
        """Отчёты с недочитанной детализацией, старшие первыми."""
        rows = await self.session.scalars(
            select(ReportModel)
            .where(ReportModel.seller_id == seller_id, ReportModel.loaded_at.is_(None))
            .order_by(ReportModel.date_from, ReportModel.report_id)
            .limit(limit)
        )
        return list(rows)

    async def pending_report_count(self, seller_id: uuid.UUID) -> int:
        return int(
            await self.session.scalar(
                select(func.count()).where(ReportModel.seller_id == seller_id, ReportModel.loaded_at.is_(None))
            )
            or 0
        )

    async def advance_report(self, seller_id: uuid.UUID, report_id: int, cursor: int) -> None:
        await self.session.execute(
            update(ReportModel)
            .where(ReportModel.seller_id == seller_id, ReportModel.report_id == report_id)
            .values(cursor=cursor)
        )

    async def finish_report(self, seller_id: uuid.UUID, report_id: int, *, now: datetime) -> None:
        await self.session.execute(
            update(ReportModel)
            .where(ReportModel.seller_id == seller_id, ReportModel.report_id == report_id)
            .values(loaded_at=now)
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

    # --- report rows ------------------------------------------------------

    async def upsert_rows(self, seller_id: uuid.UUID, rows: Iterable[ReportRow], *, now: datetime) -> int:
        values = [
            {
                "seller_id": seller_id,
                "rrd_id": row.rrd_id,
                "realizationreport_id": row.realizationreport_id,
                "date_from": row.date_from,
                "date_to": row.date_to,
                "create_dt": row.create_dt,
                "srid": row.srid,
                "assembly_id": row.assembly_id,
                "sticker_id": row.sticker_id,
                "order_dt": row.order_dt,
                "sale_dt": row.sale_dt,
                "rr_dt": row.rr_dt,
                "nm_id": row.nm_id,
                "sa_name": row.sa_name[:512],
                "subject_name": row.subject_name[:255],
                "barcode": row.barcode[:64],
                "ts_name": row.ts_name[:64],
                "bonus_type_name": row.bonus_type_name[:255],
                "supplier_oper_name": row.supplier_oper_name[:255],
                "delivery_method": row.delivery_method[:64],
                "office_name": row.office_name[:255],
                "penalty": row.penalty,
                "deduction": row.deduction,
                "rebill_logistic_cost": row.rebill_logistic_cost,
                "storage_fee": row.storage_fee,
                "additional_payment": row.additional_payment,
                "acceptance": row.acceptance,
                "collected_at": now,
            }
            for row in rows
        ]
        for offset in range(0, len(values), _CHUNK):
            statement = insert(ReportRowModel).values(values[offset : offset + _CHUNK])
            excluded = statement.excluded
            await self.session.execute(
                statement.on_conflict_do_update(
                    index_elements=["seller_id", "rrd_id"],
                    set_={
                        column: getattr(excluded, column)
                        for column in (
                            "realizationreport_id",
                            "date_from",
                            "date_to",
                            "create_dt",
                            "srid",
                            "assembly_id",
                            "sticker_id",
                            "order_dt",
                            "sale_dt",
                            "rr_dt",
                            "nm_id",
                            "sa_name",
                            "subject_name",
                            "barcode",
                            "ts_name",
                            "bonus_type_name",
                            "supplier_oper_name",
                            "delivery_method",
                            "office_name",
                            "penalty",
                            "deduction",
                            "rebill_logistic_cost",
                            "storage_fee",
                            "additional_payment",
                            "acceptance",
                            "collected_at",
                        )
                    },
                )
            )
        return len(values)

    async def rows_in_period(
        self,
        seller_id: uuid.UUID,
        date_from: date,
        date_to: date,
        *,
        group: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ReportRow]:
        """Строки по дате операции отчёта (`rr_dt`); свежие сверху, страницей — если просят.

        Группа и страница режутся в SQL: логистика за месяц — двадцать тысяч строк,
        поднимать их в память ради одной страницы не за чем.
        """
        statement = (
            select(ReportRowModel)
            .where(*self._period(seller_id, date_from, date_to, group))
            .order_by(ReportRowModel.rr_dt.desc(), ReportRowModel.rrd_id.desc())
            .offset(offset)
        )
        if limit is not None:
            statement = statement.limit(limit)
        rows = await self.session.scalars(statement)
        return [self._row(row) for row in rows]

    async def count_in_period(
        self, seller_id: uuid.UUID, date_from: date, date_to: date, *, group: str | None = None
    ) -> int:
        return (
            await self.session.scalar(
                select(func.count())
                .select_from(ReportRowModel)
                .where(*self._period(seller_id, date_from, date_to, group))
            )
            or 0
        )

    async def totals_in_period(
        self, seller_id: uuid.UUID, date_from: date, date_to: date
    ) -> dict[str, tuple[int, float]]:
        """Строк и сумма по каждой группе за период — те же правила, что у `ReportRow.group`."""
        group = self._group_expr()
        result = await self.session.execute(
            select(group, func.count(), func.sum(self._amount_expr()))
            .where(*self._period(seller_id, date_from, date_to, None))
            .group_by(group)
        )
        return {name: (int(count), round(float(amount or 0), 2)) for name, count, amount in result if name}

    def _period(self, seller_id: uuid.UUID, date_from: date, date_to: date, group: str | None) -> list:
        conditions = [
            ReportRowModel.seller_id == seller_id,
            ReportRowModel.rr_dt >= date_from,
            ReportRowModel.rr_dt <= date_to,
        ]
        if group:
            conditions.append(self._group_expr() == group)
        return conditions

    @staticmethod
    def _group_expr():  # noqa: ANN205 — выражение SQLAlchemy, тип у него не для чтения
        """`ReportRow.group` на языке SQL: первая ненулевая сумма решает, куда строка попала."""
        return case(
            (ReportRowModel.penalty != 0, GROUP_PENALTIES),
            (or_(ReportRowModel.deduction != 0, ReportRowModel.additional_payment != 0), GROUP_DEDUCTIONS),
            (ReportRowModel.rebill_logistic_cost != 0, GROUP_LOGISTICS),
            (or_(ReportRowModel.storage_fee != 0, ReportRowModel.acceptance != 0), GROUP_STORAGE),
            else_=None,
        )

    @staticmethod
    def _amount_expr():  # noqa: ANN205
        return case(
            (ReportRowModel.penalty != 0, ReportRowModel.penalty),
            (
                or_(ReportRowModel.deduction != 0, ReportRowModel.additional_payment != 0),
                ReportRowModel.deduction + ReportRowModel.additional_payment,
            ),
            (ReportRowModel.rebill_logistic_cost != 0, ReportRowModel.rebill_logistic_cost),
            (
                or_(ReportRowModel.storage_fee != 0, ReportRowModel.acceptance != 0),
                ReportRowModel.storage_fee + ReportRowModel.acceptance,
            ),
            else_=0,
        )

    async def rows_by_keys(
        self,
        seller_id: uuid.UUID,
        *,
        srids: Sequence[str] = (),
        assembly_ids: Sequence[int] = (),
        sticker_ids: Sequence[int] = (),
    ) -> list[ReportRow]:
        conditions = []
        if srids:
            conditions.append(ReportRowModel.srid.in_(set(srids)))
        if assembly_ids:
            conditions.append(ReportRowModel.assembly_id.in_(set(assembly_ids)))
        if sticker_ids:
            conditions.append(ReportRowModel.sticker_id.in_(set(sticker_ids)))
        if not conditions:
            return []
        rows = await self.session.scalars(
            select(ReportRowModel)
            .where(ReportRowModel.seller_id == seller_id, or_(*conditions))
            .order_by(ReportRowModel.rr_dt.desc(), ReportRowModel.rrd_id.desc())
        )
        return [self._row(row) for row in rows]

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
    def _row(model: ReportRowModel) -> ReportRow:
        return ReportRow(
            rrd_id=model.rrd_id,
            realizationreport_id=model.realizationreport_id,
            date_from=model.date_from,
            date_to=model.date_to,
            create_dt=model.create_dt,
            srid=model.srid,
            assembly_id=model.assembly_id,
            sticker_id=model.sticker_id,
            order_dt=model.order_dt,
            sale_dt=model.sale_dt,
            rr_dt=model.rr_dt,
            nm_id=model.nm_id,
            sa_name=model.sa_name,
            subject_name=model.subject_name,
            barcode=model.barcode,
            ts_name=model.ts_name,
            bonus_type_name=model.bonus_type_name,
            supplier_oper_name=model.supplier_oper_name,
            delivery_method=model.delivery_method,
            office_name=model.office_name,
            penalty=float(model.penalty or 0),
            deduction=float(model.deduction or 0),
            rebill_logistic_cost=float(model.rebill_logistic_cost or 0),
            storage_fee=float(model.storage_fee or 0),
            additional_payment=float(model.additional_payment or 0),
            acceptance=float(model.acceptance or 0),
        )
