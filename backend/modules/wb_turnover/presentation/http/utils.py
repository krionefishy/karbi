from backend.modules.wb_turnover.application import ArticleTurnover
from backend.modules.wb_turnover.presentation.http.schemas import ArticleTurnoverResponse


def article_response(article: ArticleTurnover) -> ArticleTurnoverResponse:
    return ArticleTurnoverResponse(
        article=article.article,
        name=article.name,
        photo_url=article.photo_url,
        stock_total=article.stock_total,
        stock_fbo=article.stock_fbo,
        stock_fbs=article.stock_fbs,
        avg_stock=article.avg_stock,
        orders_count=article.orders_count,
        avg_daily_orders=article.avg_daily_orders,
        days_of_cover=article.days_of_cover,
        turnover_days=article.turnover_days,
        stock_days=article.stock_days,
        status=article.status,
    )
