import base64

from pydantic import BaseModel, Field


class S3UploadEvent(BaseModel):
    key: str = Field(min_length=1, max_length=1024)
    content_base64: str
    content_type: str = "application/octet-stream"
    metadata: dict[str, str] = Field(default_factory=dict)

    @property
    def content(self) -> bytes:
        return base64.b64decode(self.content_base64, validate=True)

    @classmethod
    def from_bytes(
        cls,
        *,
        key: str,
        content: bytes,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> "S3UploadEvent":
        return cls(
            key=key,
            content_base64=base64.b64encode(content).decode(),
            content_type=content_type,
            metadata=metadata or {},
        )


class S3DeleteEvent(BaseModel):
    key: str = Field(min_length=1, max_length=1024)
