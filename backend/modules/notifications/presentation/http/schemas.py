"""Admin contract for bots.

The token is the one secret that legitimately passes through this server: it
arrives from the browser and leaves for the relay in the same request, and is
never stored on this side.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, SecretStr

BotCode = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]*$")]


class BotCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: BotCode
    title: Annotated[str, Field(max_length=255)] = ""
    # SecretStr keeps the token out of tracebacks and repr; the validation
    # handler in app/http keeps it out of 422 bodies.
    token: SecretStr


class BotResponse(BaseModel):
    id: str
    code: str
    title: str
    invite_link_template: str
