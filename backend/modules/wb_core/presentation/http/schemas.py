import uuid

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator


class SellerCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    api_key: SecretStr = Field(min_length=10, max_length=4096)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) < 2:
            raise ValueError("Seller name is too short")
        return normalized

    @field_validator("api_key")
    @classmethod
    def normalize_api_key(cls, value: SecretStr) -> SecretStr:
        normalized = value.get_secret_value().strip()
        if len(normalized) < 10:
            raise ValueError("API key is too short")
        return SecretStr(normalized)


class SellerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    api_key: SecretStr | None = Field(default=None, min_length=10, max_length=4096)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        return SellerCreate.normalize_name(value) if value is not None else None

    @field_validator("api_key")
    @classmethod
    def normalize_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        return SellerCreate.normalize_api_key(value) if value is not None else None

    @model_validator(mode="after")
    def at_least_one_field(self):
        if self.name is None and self.api_key is None:
            raise ValueError("At least one field is required")
        return self


class SellerRestore(BaseModel):
    api_key: SecretStr = Field(min_length=10, max_length=4096)

    @field_validator("api_key")
    @classmethod
    def normalize_api_key(cls, value: SecretStr) -> SecretStr:
        return SellerCreate.normalize_api_key(value)


class SellerResponse(BaseModel):
    id: uuid.UUID
    name: str
    product_count: int
    catalog_sync_status: str
    last_catalog_sync_at: str | None
    catalog_sync_error: str | None
    archived_at: str | None = None
    automations: list[str] = []
    # Сага доставки ключа на шлюз wb-egress.
    egress_status: str = "undelivered"
    egress_error: str | None = None
    egress_ip: str | None = None


class ArticleResponse(BaseModel):
    id: uuid.UUID
    seller_id: uuid.UUID
    article: str
    vendor_code: str
    name: str
    imt_id: int | None
    brand: str
    subject_name: str
    photo_url: str
    state: str
