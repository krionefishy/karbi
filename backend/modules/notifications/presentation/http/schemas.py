from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, SecretStr

BotCode = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]*$")]


class BotCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: BotCode
    title: Annotated[str, Field(max_length=255)] = ""
    token: SecretStr


class BotDeliveryResponse(BaseModel):
    """Когда бот последний раз доставил сообщение и сколько не ушло после этого."""

    last_sent_at: datetime | None = None
    failed_count: int = 0
    failed_since: datetime | None = None
    last_error: str | None = None


class BotResponse(BaseModel):
    id: str
    code: str
    title: str
    invite_link_template: str
    delivery: BotDeliveryResponse = BotDeliveryResponse()
