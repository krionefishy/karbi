import urllib.parse
import uuid
from datetime import date

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile, status

from backend.app.http.authentication import CurrentPrincipal
from backend.modules.fin_reports.application import (
    XLSX_MEDIA_TYPE,
    CostFileError,
    FinReportsQueryError,
    FinReportsService,
    PnlView,
)
from backend.modules.fin_reports.domain import GRANULARITY_WEEK, LINES, Statement
from backend.modules.fin_reports.presentation.http.schemas import (
    CostUploadResponse,
    LineResponse,
    PeriodResponse,
    PnlResponse,
    SellerStateResponse,
    UncostedArticleResponse,
)

router = APIRouter(prefix="/fin-reports", tags=["fin-reports"])

# Файл себестоимости — сотня строк; предел — от случайно выбранного не того файла.
MAX_COST_FILE_BYTES = 5 * 1024 * 1024


def bad_query(error: Exception) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error))


def _values(figures: Statement) -> dict[str, float]:
    return {key: float(value) for key, value in figures.values.items()}


def pnl_response(view: PnlView) -> PnlResponse:
    return PnlResponse(
        year=view.year,
        granularity=view.granularity,
        lines=[LineResponse(key=line.key, title=line.title, level=line.level) for line in LINES],
        sellers=[
            SellerStateResponse(
                seller_id=str(state.seller_id),
                name=state.name,
                reports=state.reports,
                pending_reports=state.pending_reports,
                collected_at=state.collected_at.isoformat() if state.collected_at else None,
                built_at=state.built_at.isoformat() if state.built_at else None,
                error=state.error,
            )
            for state in view.sellers
        ],
        periods=[
            PeriodResponse(
                key=column.period.key,
                label=column.label,
                date_from=column.date_from.isoformat(),
                date_to=column.date_to.isoformat(),
                values=_values(column.total),
                by_seller={str(owner): _values(figures) for owner, figures in column.by_seller.items()},
                pending_sellers=[str(owner) for owner in column.pending],
                uncosted=float(column.total.uncosted),
            )
            for column in view.periods
        ],
        total=_values(view.total),
        total_by_seller={str(owner): _values(figures) for owner, figures in view.total_by_seller.items()},
        uncosted=[
            UncostedArticleResponse(
                seller_id=str(item.seller_id),
                seller_name=item.seller_name,
                nm_id=item.nm_id,
                vendor_code=item.vendor_code,
                revenue=float(item.revenue),
            )
            for item in view.uncosted
        ],
    )


@router.get("", response_model=PnlResponse)
@inject
async def pnl(
    _: CurrentPrincipal,
    service: FromDishka[FinReportsService],
    year: int | None = Query(default=None),
    granularity: str = Query(default=GRANULARITY_WEEK),
    seller_id: uuid.UUID | None = Query(default=None),
) -> PnlResponse:
    """ОПиУ за год по неделям или месяцам: сумма по кабинетам и разбивка по каждому."""
    try:
        view = await service.view(year=year, granularity=granularity, seller_id=seller_id)
    except FinReportsQueryError as error:
        raise bad_query(error) from error
    return pnl_response(view)


@router.get("/export")
@inject
async def export_pnl(
    _: CurrentPrincipal,
    service: FromDishka[FinReportsService],
    year: int | None = Query(default=None),
    period: str | None = Query(default=None),
) -> Response:
    try:
        report = await service.export(year=year, period=period or None)
    except FinReportsQueryError as error:
        raise bad_query(error) from error
    encoded = urllib.parse.quote(report.filename)
    return Response(
        content=report.content,
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f"attachment; filename=\"{report.ascii_filename}\"; filename*=UTF-8''{encoded}"
        },
    )


@router.post("/costs", response_model=CostUploadResponse)
@inject
async def upload_costs(
    principal: CurrentPrincipal,
    service: FromDishka[FinReportsService],
    workbook: UploadFile = File(...),
    effective_from: date | None = Form(default=None),
) -> CostUploadResponse:
    """Файл себестоимости: цены действуют с указанной даты, без неё — с сегодняшнего дня."""
    content = await workbook.read(MAX_COST_FILE_BYTES + 1)
    if len(content) > MAX_COST_FILE_BYTES:
        raise HTTPException(413, "Файл себестоимости больше 5 МБ")
    try:
        result = await service.upload_costs(content, uploaded_by=principal.user_id, effective_from=effective_from)
    except (CostFileError, FinReportsQueryError) as error:
        raise bad_query(error) from error
    return CostUploadResponse(
        marketplace=result.marketplace,
        added=result.added,
        changed=result.changed,
        unchanged=result.unchanged,
        unknown_cabinets=result.unknown_cabinets,
        problems=result.problems,
        effective_from=result.effective_from.isoformat(),
    )
