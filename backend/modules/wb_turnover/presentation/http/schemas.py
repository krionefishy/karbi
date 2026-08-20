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
    days_of_cover: float | None
    turnover_days: float | None
    stock_days: int
    status: str


class TurnoverArticlesResponse(BaseModel):
    seller_id: uuid.UUID
    date: str | None
    threshold_days: int
    articles: list[ArticleTurnoverResponse]


class InviteLinkResponse(BaseModel):
    url: str
    expires_at: str
