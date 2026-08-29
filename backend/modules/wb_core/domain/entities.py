import uuid
from dataclasses import dataclass
from datetime import datetime

ARTICLE_STATES = ("active", "archived", "feedback_only")


@dataclass(frozen=True, slots=True)
class Seller:
    id: uuid.UUID
    name: str
    product_count: int
    catalog_sync_status: str
    last_catalog_sync_at: datetime | None
    catalog_sync_error: str | None
    archived_at: datetime | None = None
    egress_status: str = "undelivered"
    egress_error: str | None = None
    egress_ip: str | None = None

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None


@dataclass(frozen=True, slots=True)
class Article:
    id: uuid.UUID
    seller_id: uuid.UUID
    article: str
    vendor_code: str
    name: str
    imt_id: int | None = None
    brand: str = ""
    subject_id: int | None = None
    subject_name: str = ""
    photo_url: str = ""
    state: str = "active"
