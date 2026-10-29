from botocore.exceptions import ClientError

from backend.storage.s3 import S3Client

_MISSING = {"404", "NoSuchKey", "NotFound"}


class S3BlobStore:
    """Бакет скриншотов поверх общего `S3Client`. Оригинал отдаётся вложением, миниатюра — inline."""

    def __init__(self, client: S3Client) -> None:
        self.client = client

    @property
    def bucket(self) -> str:
        return self.client.bucket

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await self.client.put_object(key=key, body=data, content_type=content_type)

    async def get(self, key: str) -> bytes:
        return await self.client.get_object(key)

    async def size(self, key: str) -> int | None:
        try:
            head = await self.client.head_object(key)
        except ClientError as error:
            if str(error.response.get("Error", {}).get("Code")) in _MISSING:
                return None
            raise
        return int(head["ContentLength"])

    async def delete(self, key: str) -> None:
        await self.client.delete_object(key)

    def presigned_get(self, key: str, *, expires_in: int, content_type: str, attachment: bool) -> str:
        name = key.rsplit("/", 1)[-1]
        disposition = f'attachment; filename="{name}"' if attachment else "inline"
        return self.client.generate_presigned_url(
            key,
            expires_in,
            response_content_type=content_type,
            response_content_disposition=disposition,
        )
