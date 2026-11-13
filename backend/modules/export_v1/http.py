from __future__ import annotations

import hmac
import ipaddress
import json
import time
import uuid
from datetime import UTC, datetime
from typing import Annotated

import structlog
from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.http.client_ip import resolve_client_ip
from backend.infrastructure.export_v1.repository import (
    CONTRACT,
    DATASETS,
    SCHEMA_VERSION,
    BadCursor,
    ExportRepository,
    cursor_from_row,
    decode_cursor,
    encode_cursor,
    iso,
)
from backend.infrastructure.observability import ExportObservability, create_export_observability_router
from backend.infrastructure.observability.repository import (
    ProductionReportRepository,
    StaleProductionReport,
)
from backend.modules.export_v1.observability import ProductionReportRequest, record_export_page
from backend.shared.settings import Settings

router = APIRouter(prefix="/export/v1", tags=["export-v1"], include_in_schema=False)
log = structlog.get_logger("export_v1")


def error(status: int, code: str, message: str, *, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}}, headers=headers)


async def guard(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None | JSONResponse:
    settings: Settings = request.app.state.settings
    return _machine_guard(
        request,
        authorization,
        settings.export.allowed_ips,
        (settings.export.token, settings.export.token_next),
        disabled_message="export is disabled",
    )


def _machine_guard(
    request: Request,
    authorization: str | None,
    allowed_ips: tuple[str, ...],
    configured_tokens: tuple[str, ...],
    *,
    disabled_message: str,
) -> None | JSONResponse:
    settings: Settings = request.app.state.settings
    client = resolve_client_ip(request, behind_trusted_proxy=settings.app.trust_proxy_headers)
    try:
        address = ipaddress.ip_address(client)
    except ValueError:
        return error(403, "unauthorized", "access denied")
    networks = [ipaddress.ip_network(value, strict=False) for value in allowed_ips]
    if not networks or not any(address in network for network in networks):
        return error(403, "unauthorized", "access denied")

    tokens = tuple(value for value in configured_tokens if value)
    if not tokens:
        return error(503, "auth_not_configured", disabled_message)
    scheme, _, candidate = (authorization or "").partition(" ")
    accepted = scheme.lower() == "bearer" and any(hmac.compare_digest(candidate.strip(), token) for token in tokens)
    if not accepted:
        return error(401, "unauthorized", "invalid bearer token", headers={"WWW-Authenticate": "Bearer"})
    return None


def _report_guard(request: Request, source: str, authorization: str | None) -> None | JSONResponse:
    settings: Settings = request.app.state.settings
    allowed_ips = settings.export.report_bridge_ips if source == "ma_bridge" else settings.export.report_writer_ips
    return _machine_guard(
        request,
        authorization,
        allowed_ips,
        (settings.export.report_token, settings.export.report_token_next),
        disabled_message="production reporting is disabled",
    )


ExportGuard = Annotated[None | JSONResponse, Depends(guard)]


def _observer(request: Request) -> ExportObservability:
    return request.app.state.export_observability


async def _load_production_reports(request: Request):
    async with request.app.state.database.session() as session:
        return await ProductionReportRepository(session).list("wb")


def _record_http_request(
    request: Request,
    *,
    dataset: str,
    status_code: int,
    started: float,
    transfer_id: uuid.UUID,
    error_code: str | None = None,
) -> None:
    try:
        _observer(request).record_http_request(
            dataset=dataset,
            channel="wb",
            status_code=status_code,
            latency_seconds=time.monotonic() - started,
            transfer_id=transfer_id,
            error_code=error_code,
        )
    except Exception:
        log.exception(
            "export_v1_observability_failed",
            dataset=dataset,
            status_code=status_code,
            transfer_id=str(transfer_id),
        )


@router.get("/datasets")
async def datasets(auth: ExportGuard) -> JSONResponse:
    if auth is not None:
        return auth
    return JSONResponse(
        {
            "contract": CONTRACT,
            "datasets": [{"name": name, "schema_version": SCHEMA_VERSION} for name in DATASETS],
        }
    )


@router.get("/{dataset}")
@inject
async def dataset_page(
    dataset: str,
    request: Request,
    auth: ExportGuard,
    session: FromDishka[AsyncSession],
    settings: FromDishka[Settings],
) -> JSONResponse:
    started = time.monotonic()
    if auth is not None:
        return auth
    if dataset not in DATASETS:
        return error(404, "unknown_dataset", "dataset is not supported")
    transfer_id = uuid.uuid4()
    raw_limit = request.query_params.get("limit", "1000")
    try:
        limit = int(raw_limit)
    except ValueError:
        _record_http_request(
            request,
            dataset=dataset,
            status_code=400,
            started=started,
            transfer_id=transfer_id,
            error_code="bad_request",
        )
        return error(400, "bad_request", "limit must be an integer between 1 and 5000")
    if not 1 <= limit <= 5000:
        _record_http_request(
            request,
            dataset=dataset,
            status_code=400,
            started=started,
            transfer_id=transfer_id,
            error_code="bad_request",
        )
        return error(400, "bad_request", "limit must be between 1 and 5000")
    raw_cursor = request.query_params.get("cursor")
    try:
        cursor = decode_cursor(dataset, raw_cursor, settings.auth.jwt_secret) if raw_cursor else None
    except BadCursor:
        _record_http_request(
            request,
            dataset=dataset,
            status_code=400,
            started=started,
            transfer_id=transfer_id,
            error_code="bad_cursor",
        )
        return error(400, "bad_cursor", "cursor is not valid for this dataset")

    try:
        # This transaction cannot write even if a future refactor accidentally adds a DML statement.
        await session.execute(text("SET TRANSACTION READ ONLY"))
        rows, has_more = await ExportRepository(session).page(dataset, cursor, limit)
    except SQLAlchemyError:
        _record_http_request(
            request,
            dataset=dataset,
            status_code=503,
            started=started,
            transfer_id=transfer_id,
            error_code="db_unavailable",
        )
        log.exception(
            "export_v1_page_failed",
            dataset=dataset,
            status="db_unavailable",
            transfer_id=str(transfer_id),
        )
        return error(503, "db_unavailable", "export database is unavailable")
    except Exception:
        _record_http_request(
            request,
            dataset=dataset,
            status_code=500,
            started=started,
            transfer_id=transfer_id,
            error_code="internal_error",
        )
        log.exception(
            "export_v1_page_failed",
            dataset=dataset,
            status="error",
            transfer_id=str(transfer_id),
        )
        return error(500, "internal_error", "export failed")

    next_cursor = raw_cursor
    if rows:
        next_cursor = encode_cursor(dataset, cursor_from_row(rows[-1]), settings.auth.jwt_secret)
    body = {
        "contract": CONTRACT,
        "dataset": dataset,
        "schema_version": SCHEMA_VERSION,
        "items": [row.item for row in rows],
        "next_cursor": next_cursor,
        "has_more": has_more,
        "as_of": iso(datetime.now(UTC)),
    }
    encoded_size = len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode())
    duration_seconds = time.monotonic() - started
    try:
        record_export_page(
            _observer(request),
            dataset=dataset,
            rows=rows,
            next_cursor=next_cursor,
            latency_seconds=duration_seconds,
            transfer_id=transfer_id,
        )
    except Exception:
        log.exception(
            "export_v1_observability_failed",
            dataset=dataset,
            status_code=200,
            transfer_id=str(transfer_id),
        )
    log.info(
        "export_v1_page",
        dataset=dataset,
        status="ok",
        rows=len(rows),
        bytes=encoded_size,
        has_more=has_more,
        duration_ms=round(duration_seconds * 1000),
        transfer_id=str(transfer_id),
    )
    return JSONResponse(body)


@router.post("/observability/production-report")
async def production_report(
    body: ProductionReportRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    auth = _report_guard(request, body.source, authorization)
    if auth is not None:
        return auth
    try:
        async with request.app.state.database.transaction() as session:
            sent_counts = await ExportRepository(session).volume_counts() if body.source == "ma_bridge" else None
            reports = body.to_domain(sent_counts)
            repository = ProductionReportRepository(session)
            await repository.replace(reports)
            combined = await repository.list(body.channel)
    except StaleProductionReport:
        return error(409, "stale_production_report", "a newer production report is already stored")
    except SQLAlchemyError:
        log.exception(
            "export_v1_production_report_failed",
            report_id=str(body.report_id),
            status="db_unavailable",
        )
        return error(503, "db_unavailable", "production report store is unavailable")

    observer = _observer(request)
    observer.replace_production_reports(combined)
    gate = observer.production_gate_status(channel=body.channel)
    log.info(
        "export_v1_production_report_stored",
        report_id=str(body.report_id),
        observed_at=body.observed_at.isoformat(),
        channel=body.channel,
        source=body.source,
        stage=body.stage,
        datasets=len(body.datasets),
        consumer_gate_open=gate.ok,
    )
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "report_id": str(body.report_id),
            "consumer_gate_open": gate.ok,
            "production_gate_issues": [issue.code for issue in gate.issues],
        },
    )


router.include_router(
    create_export_observability_router(
        _observer,
        authorize=guard,
        production_loader=_load_production_reports,
    )
)


__all__ = ["error", "guard", "router"]
