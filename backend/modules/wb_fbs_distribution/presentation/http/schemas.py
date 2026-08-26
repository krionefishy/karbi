import uuid

from pydantic import BaseModel


class ModeRequest(BaseModel):
    write_enabled: bool


class DistributionOverviewResponse(BaseModel):
    seller_id: uuid.UUID
    mode: str
    write_enabled: bool
    enrolled_at: str
