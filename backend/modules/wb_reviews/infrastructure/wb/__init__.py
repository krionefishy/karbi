# Клиент отзывов переехал в wb_core: тем же проходом зеркало WB считает
# отзывы по карточкам. Исторические имена модуля оставлены.
from backend.modules.wb_core.infrastructure.wb import (
    FEEDBACKS_BUCKET,
    FeedbackAggregation,
    FeedbackProduct,
    WBFeedbackClient,
    WBPermanentError,
    WBTemporaryError,
)

WBFeedbackPermanentError = WBPermanentError
WBFeedbackTemporaryError = WBTemporaryError

__all__ = [
    "FEEDBACKS_BUCKET",
    "FeedbackAggregation",
    "FeedbackProduct",
    "WBFeedbackClient",
    "WBFeedbackPermanentError",
    "WBFeedbackTemporaryError",
]
