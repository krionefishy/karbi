from typing import Any

from backend.shared.kafka_streams.consumer import ConsumerHandler, kafka_subscriber
from backend.shared.kafka_streams.storage_events import S3DeleteEvent, S3UploadEvent
from backend.shared.kafka_streams.topics import StorageTopics
from backend.storage.s3 import S3Client


@kafka_subscriber(StorageTopics.WB_REVIEWS_EXPORT_UPLOAD)
async def upload_wb_reviews_export(s3: S3Client, payload: dict[str, Any]) -> None:
    event = S3UploadEvent.model_validate(payload)
    await s3.put_object(
        key=event.key,
        body=event.content,
        content_type=event.content_type,
        metadata=event.metadata,
    )


@kafka_subscriber(StorageTopics.WB_REVIEWS_EXPORT_DELETE)
async def delete_wb_reviews_export(s3: S3Client, payload: dict[str, Any]) -> None:
    event = S3DeleteEvent.model_validate(payload)
    await s3.delete_object(event.key)


s3_consumers: tuple[tuple[str, ConsumerHandler], ...] = (
    upload_wb_reviews_export,
    delete_wb_reviews_export,
)
