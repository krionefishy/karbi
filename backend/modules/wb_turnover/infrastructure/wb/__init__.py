# Клиенты остатков переехали в wb_core: их же вызывает зеркало WB. Имена
# оставлены здесь, пока оборачиваемость собирает остатки сама.
from backend.modules.wb_core.infrastructure.wb import (
    ANALYTICS_BUCKET,
    CHRT_CHUNK,
    MARKETPLACE_BUCKET,
    MAX_PAGES,
    PAGE_LIMIT,
    FBOStockRow,
    Warehouse,
    WBAnalyticsClient,
    WBMarketplaceClient,
)
from backend.modules.wb_turnover.infrastructure.wb.statistics import (
    STATISTICS_BUCKET,
    OrderRow,
    WBStatisticsClient,
)

__all__ = [
    "ANALYTICS_BUCKET",
    "CHRT_CHUNK",
    "MARKETPLACE_BUCKET",
    "MAX_PAGES",
    "PAGE_LIMIT",
    "STATISTICS_BUCKET",
    "FBOStockRow",
    "OrderRow",
    "WBAnalyticsClient",
    "WBMarketplaceClient",
    "WBStatisticsClient",
    "Warehouse",
]
