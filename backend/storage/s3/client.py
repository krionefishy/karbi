import asyncio
import re
from typing import Any

import boto3
from botocore.client import Config


def sanitize_key_segment(segment: str) -> str:
    return re.sub(r"[^\w.\-]", "_", segment)


class S3Client:
    def __init__(
        self,
        *,
        endpoint_url: str,
        tenant_id: str,
        key_id: str,
        secret_key: str,
        bucket_name: str,
        region: str = "ru-central-1",
        verify_tls: bool = True,
    ) -> None:
        self._bucket = bucket_name
        access_key = f"{tenant_id}:{key_id}" if tenant_id else key_id
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            verify=verify_tls,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    async def verify_connection(self) -> None:
        await asyncio.to_thread(self._client.head_bucket, Bucket=self._bucket)

    async def put_object(
        self,
        *,
        key: str,
        body: bytes,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> None:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
            Metadata=metadata or {},
        )

    async def get_object(self, key: str) -> bytes:
        response: dict[str, Any] = await asyncio.to_thread(
            self._client.get_object,
            Bucket=self._bucket,
            Key=key,
        )
        return await asyncio.to_thread(response["Body"].read)

    async def delete_object(self, key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=key)

    async def delete_objects(self, keys: list[str]) -> None:
        if not keys:
            return
        await asyncio.to_thread(
            self._client.delete_objects,
            Bucket=self._bucket,
            Delete={"Objects": [{"Key": key} for key in keys], "Quiet": True},
        )

    async def head_object(self, key: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._client.head_object, Bucket=self._bucket, Key=key)

    async def list_objects(self, prefix: str) -> list[dict[str, Any]]:
        response = await asyncio.to_thread(self._client.list_objects_v2, Bucket=self._bucket, Prefix=prefix)
        return response.get("Contents", [])

    def generate_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in,
        )
