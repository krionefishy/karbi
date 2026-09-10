import uuid

from pydantic import BaseModel, Field


class ChecklistItemResponse(BaseModel):
    key: str
    title: str
    meaning: str


class ItemStateResponse(BaseModel):
    key: str
    # Выполнено ли по данным WB; null — данных нет.
    done: bool | None
    detail: str | None
    note: str | None


class ChecklistRowResponse(BaseModel):
    article: str
    vendor_code: str
    barcode: str
    title: str
    photo_url: str
    subject_name: str
    card_created_at: str | None
    stock: int
    items: list[ItemStateResponse]
    done: int
    total: int
    ready: bool
    # Чего не хватает словами — тот же текст, что в колонке «Примечание» выгрузки.
    note: str
    comment: str


class ChecklistResponse(BaseModel):
    seller_id: uuid.UUID
    seller_name: str
    collected_at: str | None
    collection_error: str | None
    # ok / not_connected / stale: откуда взялся (или не взялся) отбор по остатку.
    stock_state: str
    # ok / not_connected / no_snapshot: есть ли у пунктов про отзывы данные.
    reviews_state: str
    min_stock: int
    items: list[ChecklistItemResponse]
    rows: list[ChecklistRowResponse]


class CommentRequest(BaseModel):
    text: str = Field(default="", max_length=2000)


class RefreshResponse(BaseModel):
    status: str
    in_progress: bool
    requested_at: str
    finished_at: str | None
    error: str | None
