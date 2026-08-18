from backend.modules.wb_reviews.infrastructure.wb.client import (
    FEEDBACKS_BUCKET,
    FeedbackAggregation,
    FeedbackProduct,
    WBFeedbackClient,
    WBFeedbackPermanentError,
    WBFeedbackTemporaryError,
)

__all__ = [
    "FEEDBACKS_BUCKET",
    "FeedbackAggregation",
    "FeedbackProduct",
    "WBFeedbackClient",
    "WBFeedbackPermanentError",
    "WBFeedbackTemporaryError",
]
