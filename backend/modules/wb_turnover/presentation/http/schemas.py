import uuid

from pydantic import BaseModel


class ArticleTurnoverResponse(BaseModel):
    article: str
    name: str
    photo_url: str
    stock_total: int
    stock_fbo: int
    stock_fbs: int
    avg_stock: float
    orders_count: int
    avg_daily_orders: float
    days_of_cover: int | None
    turnover_days: int | None
    stock_days: int
    status: str


class TurnoverArticlesResponse(BaseModel):
    seller_id: uuid.UUID
    date: str | None
    threshold_days: int
    # За сколько дней считался темп продаж: интерфейс подписывает этим числом
    # колонки, чтобы окно можно было менять настройкой, а не правкой вёрстки.
    window_days: int
    articles: list[ArticleTurnoverResponse]


class InviteLinkResponse(BaseModel):
    url: str
    expires_at: str


class RefreshResponse(BaseModel):
    """State of the «обновить данные» request the interface polls."""

    status: str
    in_progress: bool
    requested_at: str
    finished_at: str | None
    error: str | None
