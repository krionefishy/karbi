import uuid

from pydantic import BaseModel, Field, SecretStr, model_validator


class AutomationRunResponse(BaseModel):
    id: uuid.UUID
    trigger: str
    status: str
    snapshot_date: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    total_sellers: int
    completed_sellers: int
    failed_sellers: int
    duration_seconds: int | None


class AutomationResponse(BaseModel):
    id: str
    title: str
    description: str
    status: str
    seller_count: int
    runs_last_24h: int
    last_run: AutomationRunResponse | None
    last_success_at: str | None
    next_run_at: str | None


class AutomationSellerAttach(BaseModel):
    """Connect a seller to an automation: an existing one, or a brand new one."""

    seller_id: uuid.UUID | None = None
    name: str | None = Field(default=None, min_length=2, max_length=255)
    api_key: SecretStr | None = Field(default=None, min_length=10, max_length=4096)

    @model_validator(mode="after")
    def exactly_one_form(self):
        creating = self.name is not None or self.api_key is not None
        if bool(self.seller_id) == creating:
            raise ValueError("Provide either seller_id or both name and api_key")
        if creating and (self.name is None or self.api_key is None):
            raise ValueError("A new seller needs both name and api_key")
        return self
