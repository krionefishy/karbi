import logging
import uuid
from dataclasses import asdict
from datetime import datetime

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException, Query, Response, status

from backend.app.http.principals import CurrentPrincipal
from backend.modules.wb_core.application import SellerNotFoundError
from backend.modules.wb_support_bot.application import (
    BotRequestError,
    NotEnrolledError,
    SendRow,
    SupportBotService,
)
from backend.modules.wb_support_bot.application.reply_stats import ArmStats
from backend.modules.wb_support_bot.application.service import ReplyOverview, reply_action
from backend.modules.wb_support_bot.domain import chat_url
from backend.modules.wb_support_bot.infrastructure.postgres import (
    VERDICT_DONE,
    DialogModel,
    ReplyVerdictModel,
    TemplateModel,
    VerdictModel,
)
from backend.modules.wb_support_bot.presentation.http.schemas import (
    ArmStatsResponse,
    CategoryCountResponse,
    ContinueBucketResponse,
    ContinuesResponse,
    DailyPointResponse,
    DialogActionRequest,
    DialogPageResponse,
    DialogResponse,
    EffectResponse,
    EscalationListResponse,
    EscalationResponse,
    HealthResponse,
    LateSendsResponse,
    ManualVerdictRequest,
    ModeRequest,
    PauseRequest,
    ReplyDialogResponse,
    ReplyMessageResponse,
    ReplySummaryResponse,
    ReplyVerdictResponse,
    SendPageResponse,
    SendResponse,
    SupportBotResponse,
    TemplateActiveRequest,
    TemplateRequest,
    TemplateResponse,
)
from backend.shared.settings import Settings

router = APIRouter(prefix="/wb/support-bot", tags=["wb-support-bot"])


def not_enrolled() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Селлер не подключён к автоматизации")


def bad_request(error: BotRequestError) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error))


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def template_response(model: TemplateModel) -> TemplateResponse:
    return TemplateResponse(id=model.id, text=model.text, active=model.active)


def dialog_response(
    model: DialogModel,
    chat_url_template: str,
    verdicts: dict[tuple[str, str], VerdictModel] | None = None,
    replies: dict[str, ReplyVerdictModel] | None = None,
) -> DialogResponse:
    row = (verdicts or {}).get((model.chat_id, model.tail_fingerprint)) if model.tail_fingerprint else None
    done = row is not None and row.status == VERDICT_DONE
    manual = model.manual_verdict if model.manual_fingerprint == model.tail_fingerprint else None
    reply = (replies or {}).get(model.chat_id)
    return DialogResponse(
        chat_id=model.chat_id,
        prompt_at=model.prompt_at.isoformat(),
        state=model.state,
        nm_id=model.nm_id,
        buyer_first_at=_iso(model.buyer_first_at),
        buyer_last_at=_iso(model.buyer_last_at),
        our_last_at=_iso(model.our_last_at),
        campaign_started_at=_iso(model.campaign_started_at),
        next_send_at=_iso(model.next_send_at),
        sent_count=model.sent_count,
        last_sent_at=_iso(model.last_sent_at),
        no_contact=model.no_contact,
        attention=model.attention,
        excluded=model.excluded,
        closed=model.closed,
        chat_url=chat_url(chat_url_template, model.chat_id),
        verdict=row.verdict if row is not None and done else None,
        verdict_reason=row.reason if row is not None and done else None,
        manual_verdict=manual,
        reply_hold=model.reply_hold,
        reply_category=(reply.guard or reply.category) if reply is not None and reply.status == VERDICT_DONE else None,
    )


def send_response(row: SendRow, chat_url_template: str) -> SendResponse:
    model = row.send
    return SendResponse(
        id=model.id,
        chat_id=model.chat_id,
        prompt_at=model.prompt_at.isoformat(),
        nm_id=row.nm_id,
        chat_url=chat_url(chat_url_template, model.chat_id),
        seq=model.seq,
        text=model.text,
        status=model.status,
        planned_at=model.planned_at.isoformat(),
        sent_at=_iso(model.sent_at),
        error=model.error,
    )


@router.get("/sellers/{seller_id}", response_model=SupportBotResponse)
@inject
async def seller_view(
    seller_id: uuid.UUID, _: CurrentPrincipal, service: FromDishka[SupportBotService]
) -> SupportBotResponse:
    try:
        view = await service.view(seller_id)
    except (SellerNotFoundError, NotEnrolledError) as error:
        raise not_enrolled() from error
    return SupportBotResponse(
        seller_id=str(view.seller_id),
        seller_name=view.seller_name,
        mode=view.mode,
        paused_reason=view.paused_reason,
        globally_paused=view.globally_paused,
        counts=view.counts,
        templates=[template_response(item) for item in view.templates],
        mirror_error=view.mirror_error,
        mirror_read_through=_iso(view.mirror_read_through),
        classifier_mode=view.classifier_mode,
        reply_gate_mode=view.reply_gate_mode,
    )


@router.get("/sellers/{seller_id}/dialogs", response_model=DialogPageResponse)
@inject
async def dialogs(
    seller_id: uuid.UUID,
    _: CurrentPrincipal,
    service: FromDishka[SupportBotService],
    settings: FromDishka[Settings],
    state: str | None = None,
    flag: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> DialogPageResponse:
    try:
        result = await service.dialogs(seller_id, state=state, flag=flag, page=page, page_size=page_size)
    except (SellerNotFoundError, NotEnrolledError) as error:
        raise not_enrolled() from error
    except BotRequestError as error:
        raise bad_request(error) from error
    template = settings.support_bot.chat_url_template
    return DialogPageResponse(
        dialogs=[dialog_response(item, template, result.verdicts, result.replies) for item in result.dialogs],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


@router.get("/sellers/{seller_id}/sends", response_model=SendPageResponse)
@inject
async def sends(
    seller_id: uuid.UUID,
    _: CurrentPrincipal,
    service: FromDishka[SupportBotService],
    settings: FromDishka[Settings],
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> SendPageResponse:
    try:
        result = await service.sends(seller_id, status=status_filter, page=page, page_size=page_size)
    except (SellerNotFoundError, NotEnrolledError) as error:
        raise not_enrolled() from error
    except BotRequestError as error:
        raise bad_request(error) from error
    template = settings.support_bot.chat_url_template
    return SendPageResponse(
        sends=[send_response(item, template) for item in result.rows],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
        counts=result.counts,
    )


@router.put("/sellers/{seller_id}/mode", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def set_mode(
    seller_id: uuid.UUID, payload: ModeRequest, _: CurrentPrincipal, service: FromDishka[SupportBotService]
) -> Response:
    try:
        await service.set_mode(seller_id, payload.mode)
    except (SellerNotFoundError, NotEnrolledError) as error:
        raise not_enrolled() from error
    except BotRequestError as error:
        raise bad_request(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/control", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def set_paused(payload: PauseRequest, _: CurrentPrincipal, service: FromDishka[SupportBotService]) -> Response:
    await service.set_paused(payload.paused)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sellers/{seller_id}/templates", response_model=TemplateResponse, status_code=status.HTTP_201_CREATED)
@inject
async def add_template(
    seller_id: uuid.UUID, payload: TemplateRequest, _: CurrentPrincipal, service: FromDishka[SupportBotService]
) -> TemplateResponse:
    try:
        return template_response(await service.add_template(seller_id, payload.text))
    except (SellerNotFoundError, NotEnrolledError) as error:
        raise not_enrolled() from error
    except BotRequestError as error:
        raise bad_request(error) from error


@router.put("/sellers/{seller_id}/templates/{template_id}", response_model=TemplateResponse)
@inject
async def update_template(
    seller_id: uuid.UUID,
    template_id: int,
    payload: TemplateRequest,
    _: CurrentPrincipal,
    service: FromDishka[SupportBotService],
) -> TemplateResponse:
    try:
        return template_response(await service.update_template_text(seller_id, template_id, payload.text))
    except (SellerNotFoundError, NotEnrolledError) as error:
        raise not_enrolled() from error
    except BotRequestError as error:
        raise bad_request(error) from error


@router.put("/sellers/{seller_id}/templates/{template_id}/active", response_model=TemplateResponse)
@inject
async def set_template_active(
    seller_id: uuid.UUID,
    template_id: int,
    payload: TemplateActiveRequest,
    _: CurrentPrincipal,
    service: FromDishka[SupportBotService],
) -> TemplateResponse:
    try:
        return template_response(await service.set_template_active(seller_id, template_id, payload.active))
    except (SellerNotFoundError, NotEnrolledError) as error:
        raise not_enrolled() from error
    except BotRequestError as error:
        raise bad_request(error) from error


@router.delete("/sellers/{seller_id}/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def delete_template(
    seller_id: uuid.UUID, template_id: int, _: CurrentPrincipal, service: FromDishka[SupportBotService]
) -> Response:
    try:
        await service.delete_template(seller_id, template_id)
    except (SellerNotFoundError, NotEnrolledError) as error:
        raise not_enrolled() from error
    except BotRequestError as error:
        raise bad_request(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sellers/{seller_id}/dialogs/flags", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def dialog_flags(
    seller_id: uuid.UUID, payload: DialogActionRequest, _: CurrentPrincipal, service: FromDishka[SupportBotService]
) -> Response:
    try:
        prompt_at = datetime.fromisoformat(payload.prompt_at)
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Неверная дата диалога") from error
    try:
        await service.set_dialog_flags(
            seller_id, payload.chat_id, prompt_at, excluded=payload.excluded, closed=payload.closed
        )
    except (SellerNotFoundError, NotEnrolledError) as error:
        raise not_enrolled() from error
    except BotRequestError as error:
        raise bad_request(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sellers/{seller_id}/dialogs/manual-verdict", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def dialog_manual_verdict(
    seller_id: uuid.UUID, payload: ManualVerdictRequest, _: CurrentPrincipal, service: FromDishka[SupportBotService]
) -> Response:
    try:
        prompt_at = datetime.fromisoformat(payload.prompt_at)
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Неверная дата диалога") from error
    try:
        await service.set_manual_verdict(seller_id, payload.chat_id, prompt_at, payload.value)
    except (SellerNotFoundError, NotEnrolledError) as error:
        raise not_enrolled() from error
    except BotRequestError as error:
        raise bad_request(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _summary_response(overview: ReplyOverview) -> ReplySummaryResponse:
    summary = overview.summary

    def arm(stats: ArmStats) -> ArmStatsResponse:
        return ArmStatsResponse(
            dialogs=stats.dialogs,
            eligible24=stats.eligible24,
            replied24=stats.replied24,
            eligible48=stats.eligible48,
            replied48=stats.replied48,
        )

    def counts(items: list[tuple[str, int]]) -> list[CategoryCountResponse]:
        return [CategoryCountResponse(category=category, count=count) for category, count in items]

    return ReplySummaryResponse(
        generated_at=overview.generated_at.isoformat(),
        days=summary.days,
        gate_mode=overview.gate_mode,
        live_since=_iso(overview.live_since),
        effect=EffectResponse(live=arm(summary.effect_live), control=arm(summary.effect_control)),
        categories=counts(summary.categories),
        categories_slices=counts(summary.categories_slices),
        categories_before=counts(summary.categories_before),
        categories_after=counts(summary.categories_after),
        actions=summary.actions,
        daily=[DailyPointResponse(**asdict(point)) for point in summary.daily],
        escalations_open=summary.escalations_open,
        late_sends=LateSendsResponse(
            count=summary.late_sends.count,
            total_sends=summary.late_sends.total_sends,
            median_lag_minutes=summary.late_sends.median_lag_minutes,
            max_lag_minutes=summary.late_sends.max_lag_minutes,
        ),
        continues=ContinuesResponse(
            dialogs=summary.continues.dialogs,
            escalated_after=summary.continues.escalated_after,
            resolved_after=summary.continues.resolved_after,
            max_per_dialog=summary.continues.max_per_dialog,
            distribution=[
                ContinueBucketResponse(count=count, dialogs=dialogs)
                for count, dialogs in summary.continues.distribution
            ],
        ),
        health=HealthResponse(**asdict(summary.health)),
    )


@router.get("/sellers/{seller_id}/replies/summary", response_model=ReplySummaryResponse)
@inject
async def replies_summary(
    seller_id: uuid.UUID,
    _: CurrentPrincipal,
    service: FromDishka[SupportBotService],
    days: int = Query(default=14, ge=1, le=400),
) -> ReplySummaryResponse:
    try:
        return _summary_response(await service.reply_overview(seller_id, days=days))
    except (SellerNotFoundError, NotEnrolledError) as error:
        raise not_enrolled() from error
    except BotRequestError as error:
        raise bad_request(error) from error


@router.get("/sellers/{seller_id}/replies/escalations", response_model=EscalationListResponse)
@inject
async def replies_escalations(
    seller_id: uuid.UUID,
    _: CurrentPrincipal,
    service: FromDishka[SupportBotService],
    settings: FromDishka[Settings],
    days: int = Query(default=30, ge=1, le=400),
) -> EscalationListResponse:
    try:
        rows = await service.reply_escalations(seller_id, days=days)
    except (SellerNotFoundError, NotEnrolledError) as error:
        raise not_enrolled() from error
    template = settings.support_bot.chat_url_template
    return EscalationListResponse(
        items=[
            EscalationResponse(
                chat_id=row.chat_id,
                prompt_at=row.prompt_at.isoformat(),
                nm_id=row.nm_id,
                category=row.category,
                reason=row.reason,
                event_at=row.event_at.isoformat(),
                state=row.state,
                closed=row.closed,
                excluded=row.excluded,
                chat_url=chat_url(template, row.chat_id),
            )
            for row in rows
        ]
    )


@router.get("/sellers/{seller_id}/replies/dialog", response_model=ReplyDialogResponse)
@inject
async def replies_dialog(
    seller_id: uuid.UUID,
    principal: CurrentPrincipal,
    service: FromDishka[SupportBotService],
    settings: FromDishka[Settings],
    chat_id: str,
    prompt_at: str,
) -> ReplyDialogResponse:
    """Единственное место с текстами переписки: открытие карточки пишется в журнал без текста."""
    try:
        moment = datetime.fromisoformat(prompt_at)
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Неверная дата диалога") from error
    try:
        card = await service.reply_dialog(seller_id, chat_id, moment)
    except (SellerNotFoundError, NotEnrolledError) as error:
        raise not_enrolled() from error
    except BotRequestError as error:
        raise bad_request(error) from error
    logging.getLogger("wb.support_bot.reply_text").info(
        "reply_dialog_opened",
        extra={"user_id": str(principal.user_id), "seller_id": str(seller_id), "chat_id": chat_id},
    )
    return ReplyDialogResponse(
        chat_id=chat_id,
        prompt_at=moment.isoformat(),
        chat_url=chat_url(settings.support_bot.chat_url_template, chat_id),
        messages=[
            ReplyMessageResponse(at=item.at.isoformat(), role=item.role, text=item.text) for item in card.messages
        ],
        verdicts=[
            ReplyVerdictResponse(
                first_event_at=row.first_event_at.isoformat(),
                last_event_at=row.last_event_at.isoformat(),
                messages=row.messages,
                status=row.status,
                category=row.guard or row.category,
                reason=row.reason,
                action=reply_action(row).value,
            )
            for row in card.verdicts
        ],
    )
