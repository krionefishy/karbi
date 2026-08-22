"""The inbound half of the relay contract.

Nothing here is messenger-specific: `recipient` is an opaque address, the sender
carries an external id we do not interpret, and `event_id` is only ever compared
with the cursor.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class SenderPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    external_id: str | None = None
    username: str = ""
    display_name: str = ""


class UpdatePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    bot_code: Annotated[str, Field(min_length=1, max_length=64)]
    event_id: Annotated[int, Field(ge=0)]
    recipient: Annotated[str, Field(min_length=1, max_length=128)]
    text: Annotated[str, Field(max_length=8192)] = ""
    sender: SenderPayload = SenderPayload()


class UpdateAccepted(BaseModel):
    accepted: bool
