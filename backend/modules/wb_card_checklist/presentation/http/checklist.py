import urllib.parse
import uuid

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException, Response, status

from backend.app.http.authentication import CurrentPrincipal
from backend.modules.wb_card_checklist.application import (
    XLSX_MEDIA_TYPE,
    ArticleNotInChecklistError,
    ChecklistRow,
    ChecklistService,
    ChecklistView,
    RefreshRequest,
)
from backend.modules.wb_card_checklist.domain import ITEMS
from backend.modules.wb_card_checklist.presentation.http.schemas import (
    ChecklistItemResponse,
    ChecklistResponse,
    ChecklistRowResponse,
    CommentRequest,
    ItemStateResponse,
    RefreshResponse,
)
from backend.modules.wb_core.application import SellerNotFoundError

router = APIRouter(prefix="/wb/card-checklist", tags=["wb-card-checklist"])


def not_enrolled() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Селлер не подключён к автоматизации")


def row_response(row: ChecklistRow) -> ChecklistRowResponse:
    return ChecklistRowResponse(
        article=row.article,
        vendor_code=row.vendor_code,
        barcode=row.barcode,
        title=row.title,
        photo_url=row.photo_url,
        subject_name=row.subject_name,
        card_created_at=row.card_created_at.isoformat() if row.card_created_at else None,
        stock=row.stock,
        items=[
            ItemStateResponse(key=item.key, done=item.done, detail=item.detail, note=item.note) for item in row.items
        ],
        done=row.done,
        total=row.total,
        ready=row.ready,
        comment=row.comment,
    )


def checklist_response(view: ChecklistView) -> ChecklistResponse:
    return ChecklistResponse(
        seller_id=view.seller_id,
        seller_name=view.seller_name,
        collected_at=view.collected_at.isoformat() if view.collected_at else None,
        collection_error=view.collection_error,
        stock_state=view.stock_state,
        reviews_state=view.reviews_state,
        min_stock=view.min_stock,
        items=[ChecklistItemResponse(key=item.key, title=item.title, meaning=item.meaning) for item in ITEMS],
        rows=[row_response(row) for row in view.rows],
    )


def refresh_response(state: RefreshRequest) -> RefreshResponse:
    return RefreshResponse(
        status=state.status,
        in_progress=state.in_progress,
        requested_at=state.requested_at.isoformat(),
        finished_at=state.finished_at.isoformat() if state.finished_at else None,
        error=state.error,
    )


@router.get("/sellers/{seller_id}", response_model=ChecklistResponse)
@inject
async def seller_checklist(
    seller_id: uuid.UUID, _: CurrentPrincipal, service: FromDishka[ChecklistService]
) -> ChecklistResponse:
    try:
        view = await service.view(seller_id)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    return checklist_response(view)


@router.put("/sellers/{seller_id}/articles/{article}/comment", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def set_comment(
    seller_id: uuid.UUID,
    article: str,
    payload: CommentRequest,
    principal: CurrentPrincipal,
    service: FromDishka[ChecklistService],
) -> Response:
    try:
        await service.set_comment(seller_id, article, payload.text, principal.user_id)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    except ArticleNotInChecklistError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Этого товара сейчас нет в чек-листе") from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sellers/{seller_id}/refresh", response_model=RefreshResponse, status_code=status.HTTP_202_ACCEPTED)
@inject
async def request_refresh(
    seller_id: uuid.UUID, principal: CurrentPrincipal, service: FromDishka[ChecklistService]
) -> RefreshResponse:
    """Ask for an out-of-schedule collection. Pressing twice returns the same request."""
    try:
        state = await service.request_refresh(seller_id, requested_by=principal.user_id)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    return refresh_response(state)


@router.get("/sellers/{seller_id}/refresh", response_model=RefreshResponse | None)
@inject
async def refresh_state(
    seller_id: uuid.UUID, _: CurrentPrincipal, service: FromDishka[ChecklistService]
) -> RefreshResponse | None:
    try:
        state = await service.refresh_state(seller_id)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    return refresh_response(state) if state else None


@router.get("/sellers/{seller_id}/export")
@inject
async def export_checklist(
    seller_id: uuid.UUID, _: CurrentPrincipal, service: FromDishka[ChecklistService]
) -> Response:
    """Таблица в формате, который менеджеры вели руками, — те же колонки и формулы."""
    try:
        report = await service.export(seller_id)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    # Имя с кириллицей уезжает в filename*, а ascii-вариант остаётся запасным.
    fallback = f"checklist_{report.day.isoformat()}.xlsx"
    encoded = urllib.parse.quote(report.filename)
    return Response(
        content=report.content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"},
    )
