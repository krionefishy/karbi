import re

_TOPIC_NAME = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")


def validate_topic_name(topic: str) -> str:
    if not _TOPIC_NAME.fullmatch(topic):
        raise ValueError(f"Invalid Kafka topic name: {topic!r}")
    return topic


class StorageTopics:
    """S3 mutations use storage.s3.<domain>.<action>; one action per topic."""

    WB_REVIEWS_EXPORT_UPLOAD = validate_topic_name("storage.s3.wb-reviews-export.upload")
    WB_REVIEWS_EXPORT_DELETE = validate_topic_name("storage.s3.wb-reviews-export.delete")

    @classmethod
    def all(cls) -> tuple[str, ...]:
        return (cls.WB_REVIEWS_EXPORT_UPLOAD, cls.WB_REVIEWS_EXPORT_DELETE)


class WBCoreTopics:
    CATALOG_SYNC_REQUESTED = validate_topic_name("wb.catalog.sync.requested")

    @classmethod
    def all(cls) -> tuple[str, ...]:
        return (cls.CATALOG_SYNC_REQUESTED,)


class WBReviewsTopics:
    SYNC_REQUESTED = validate_topic_name("wb.reviews.sync.requested")

    @classmethod
    def all(cls) -> tuple[str, ...]:
        return (cls.SYNC_REQUESTED,)


def all_topics() -> tuple[str, ...]:
    return (*StorageTopics.all(), *WBCoreTopics.all(), *WBReviewsTopics.all())
