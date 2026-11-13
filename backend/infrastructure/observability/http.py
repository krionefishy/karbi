from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST

from backend.infrastructure.observability.export_v1 import (
    ExportHealthThresholds,
    ExportObservability,
    ProductionVolumeReport,
)


def create_export_observability_router(
    observer: ExportObservability | Callable[[Request], ExportObservability],
    *,
    authorize: Callable[..., Any],
    thresholds: ExportHealthThresholds = ExportHealthThresholds(),
    production_loader: Callable[[Request], Awaitable[list[ProductionVolumeReport]]] | None = None,
) -> APIRouter:
    """Build endpoints that cannot be mounted without an explicit auth dependency.

    KRB-39 owns the machine-token and vm-bridge network policy.  Keeping the
    dependency mandatory prevents an observability-only change from publishing
    seller-level metrics before that policy exists.
    """

    router = APIRouter(prefix="/observability", tags=["export-v1-observability"])

    def resolve(request: Request) -> ExportObservability:
        return observer(request) if callable(observer) else observer

    @router.get("/metrics", include_in_schema=False)
    async def metrics(request: Request, auth: Any = Depends(authorize)) -> Response:
        if isinstance(auth, Response):
            return auth
        return Response(
            content=resolve(request).render_prometheus(),
            headers={"Content-Type": CONTENT_TYPE_LATEST},
        )

    @router.get("/status", include_in_schema=False)
    async def status(request: Request, auth: Any = Depends(authorize)) -> Response:
        if isinstance(auth, Response):
            return auth
        current_observer = resolve(request)
        if production_loader is not None:
            try:
                current_observer.replace_production_reports(await production_loader(request))
            except Exception:
                return JSONResponse(
                    status_code=503,
                    content={
                        "ok": False,
                        "pipeline_issues": [],
                        "production_gate_issues": [
                            {
                                "code": "production_report_store_unavailable",
                                "dataset": "all",
                                "seller_id": None,
                                "channel": "wb",
                                "value": 1.0,
                                "stage": None,
                            }
                        ],
                    },
                )
        current = current_observer.status(thresholds)
        gate = current_observer.production_gate_status()
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
