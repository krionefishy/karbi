from backend.modules.wb_core.infrastructure.wb import WBThrottle, budgets_for
from backend.modules.wb_fbs_distribution.infrastructure.wb.marketplace import MARKETPLACE_BUCKET
from backend.shared.settings import Settings
from backend.storage.redis import RedisClient


def marketplace_throttle(settings: Settings, redis: RedisClient) -> WBThrottle:
    """Общий с остальными модулями бюджет Marketplace API.

    Бюджет живёт в Redis и считается по ключу селлера, поэтому воркер
    распределения и оборачиваемость делят один лимит, а не выедают его вслепую
    друг у друга.
    """
    wb_api = settings.wb_api
    return WBThrottle(
        budgets=budgets_for(
            MARKETPLACE_BUCKET,
            per_key=wb_api.marketplace_per_key,
            per_host=wb_api.marketplace_per_host,
            window_seconds=wb_api.window_seconds,
        ),
        redis_client=redis,
        max_wait_seconds=wb_api.max_wait_seconds,
    )
