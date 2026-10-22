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
    # Скрины ДБС: подписчики в wb-screenshot-control-worker, файл берут из staging, а не из события.
    WB_SCREENSHOT_CONTROL_UPLOAD = validate_topic_name("storage.s3.wb-screenshot-control.upload")
    WB_SCREENSHOT_CONTROL_DELETE = validate_topic_name("storage.s3.wb-screenshot-control.delete")

    @classmethod
    def all(cls) -> tuple[str, ...]:
        return (
            cls.WB_REVIEWS_EXPORT_UPLOAD,
            cls.WB_REVIEWS_EXPORT_DELETE,
            cls.WB_SCREENSHOT_CONTROL_UPLOAD,
            cls.WB_SCREENSHOT_CONTROL_DELETE,
        )


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


class NotificationTopics:
    """Outgoing notifications. The payload names the bot to speak through."""

    TELEGRAM_MESSAGE_REQUESTED = validate_topic_name("notifications.telegram.message.requested")
    TELEGRAM_COMMAND_RECEIVED = validate_topic_name("notifications.telegram.command.received")

    @classmethod
    def all(cls) -> tuple[str, ...]:
        return (cls.TELEGRAM_MESSAGE_REQUESTED, cls.TELEGRAM_COMMAND_RECEIVED)


class SupportBotTopics:
    """Запросы вердикта «диалог завершён нашей стороной» для нейросети: бот не ждёт модель, а кладёт запрос в outbox."""

    CLASSIFY_REQUESTED = validate_topic_name("wb.support-bot.classify.requested")

    @classmethod
    def all(cls) -> tuple[str, ...]:
        return (cls.CLASSIFY_REQUESTED,)


class ScreenshotControlTopics:
    """Конвейер скриншотов ДБС: приём → распознавание → решение. `extracted` идёт одним ключом партиции."""

    SUBMITTED = validate_topic_name("wb.screenshot-control.screenshot.submitted")
    EXTRACTED = validate_topic_name("wb.screenshot-control.screenshot.extracted")
    VERDICT_READY = validate_topic_name("wb.screenshot-control.screenshot.verdict-ready")

    @classmethod
    def all(cls) -> tuple[str, ...]:
        return (cls.SUBMITTED, cls.EXTRACTED, cls.VERDICT_READY)


def all_topics() -> tuple[str, ...]:
    return (
        *StorageTopics.all(),
        *WBCoreTopics.all(),
        *WBReviewsTopics.all(),
        *NotificationTopics.all(),
        *SupportBotTopics.all(),
        *ScreenshotControlTopics.all(),
    )
