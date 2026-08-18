import uuid

from pydantic import BaseModel


class SyncRunResponse(BaseModel):
    id: uuid.UUID
    trigger: str
    snapshot_date: str
    status: str
    total_sellers: int
    completed_sellers: int
    failed_sellers: int
    created_at: str
    started_at: str | None
    finished_at: str | None
    jobs: list["SyncJobResponse"]


class SyncJobResponse(BaseModel):
    id: uuid.UUID
    seller_id: uuid.UUID
    seller_name: str
    status: str
    product_count: int
    feedback_count: int
    error: str | None
    started_at: str | None
    finished_at: str | None
    attempts: int


class SnapshotResponse(BaseModel):
    date: str
    ratings: dict[int, int]


class ProductHistoryResponse(BaseModel):
    id: uuid.UUID
    article: str
    vendor_code: str
    name: str
    imt_id: int | None
    brand: str
    photo_url: str
    state: str
    snapshots: list[SnapshotResponse]
    card_snapshots: list[SnapshotResponse]


class ReviewHistoryResponse(BaseModel):
    seller_id: uuid.UUID
    products: list[ProductHistoryResponse]
