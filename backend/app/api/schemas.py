import uuid

from pydantic import BaseModel


class AutomationRunResponse(BaseModel):
    id: uuid.UUID
    trigger: str
    status: str
    snapshot_date: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    total_sellers: int
    completed_sellers: int
    failed_sellers: int
    duration_seconds: int | None


class AutomationResponse(BaseModel):
    id: str
    title: str
    description: str
    status: str
    seller_count: int
    runs_last_24h: int
    last_run: AutomationRunResponse | None
    last_success_at: str | None
    next_run_at: str | None
