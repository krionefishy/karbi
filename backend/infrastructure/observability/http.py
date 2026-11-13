from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST

from backend.infrastructure.observability.export_v1 import ExportHealthThresholds, ExportObservability


def create_export_observability_router(
    observer: ExportObservability,
    *,
    authorize: Callable[..., Any],
    thresholds: ExportHealthThresholds = ExportHealthThresholds(),
) -> APIRouter:
    """Build endpoints that cannot be mounted without an explicit auth dependency.

    KRB-39 owns the machine-token and vm-bridge network policy.  Keeping the
    dependency mandatory prevents an observability-only change from publishing
    seller-level metrics before that policy exists.
    """

    router = APIRouter(
        prefix="/observability",
        tags=["export-v1-observability"],
        dependencies=[Depends(authorize)],
    )

    @router.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(
            content=observer.render_prometheus(),
            headers={"Content-Type": CONTENT_TYPE_LATEST},
        )

    @router.get("/status", include_in_schema=False)
    async def status() -> JSONResponse:
        current = observer.status(thresholds)
        gate = observer.production_gate_status()
        ok = current.ok and gate.ok
        return JSONResponse(
            status_code=200 if ok else 503,
            content={
                "ok": ok,
                "checked_at": current.checked_at.isoformat(),
                "pipeline_issues": [asdict(issue) for issue in current.issues],
                "production_gate_issues": [asdict(issue) for issue in gate.issues],
            },
        )

    return router
