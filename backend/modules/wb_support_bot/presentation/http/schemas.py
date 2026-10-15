from pydantic import BaseModel, Field


class TemplateResponse(BaseModel):
    id: int
    text: str
    active: bool


class SupportBotResponse(BaseModel):
    seller_id: str
    seller_name: str
    mode: str
    paused_reason: str | None
    globally_paused: bool
    counts: dict[str, int]
    templates: list[TemplateResponse]
    mirror_error: str | None
    mirror_read_through: str | None
    canary_percent: int


class DialogResponse(BaseModel):
    chat_id: str
    prompt_at: str
    state: str
    nm_id: int | None
    buyer_first_at: str | None
    buyer_last_at: str | None
    our_last_at: str | None
    campaign_started_at: str | None
    next_send_at: str | None
    sent_count: int
    last_sent_at: str | None
    no_contact: bool
    attention: bool
    excluded: bool
    closed: bool
    chat_url: str | None


class DialogPageResponse(BaseModel):
    dialogs: list[DialogResponse]
    total: int
    page: int
    page_size: int


class SendResponse(BaseModel):
    id: int
    chat_id: str
    prompt_at: str
    nm_id: int | None
    chat_url: str | None
    seq: int
    text: str
    status: str
    planned_at: str
    sent_at: str | None
    error: str | None


class SendPageResponse(BaseModel):
    sends: list[SendResponse]
    total: int
    page: int
    page_size: int
    counts: dict[str, int]


class ModeRequest(BaseModel):
    mode: str


class PauseRequest(BaseModel):
    paused: bool


class TemplateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class TemplateActiveRequest(BaseModel):
    active: bool


class DialogActionRequest(BaseModel):
    chat_id: str
    prompt_at: str
    excluded: bool | None = None
    closed: bool | None = None
