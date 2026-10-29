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
    classifier_mode: str
    reply_gate_mode: str
    monitor_overall: str


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
    verdict: str | None = None
    verdict_reason: str | None = None
    manual_verdict: str | None = None
    reply_hold: bool = False
    reply_category: str | None = None


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


class ManualVerdictRequest(BaseModel):
    chat_id: str
    prompt_at: str
    value: str | None = None


class ArmStatsResponse(BaseModel):
    dialogs: int
    eligible24: int
    replied24: int
    eligible48: int
    replied48: int


class EffectResponse(BaseModel):
    live: ArmStatsResponse
    control: ArmStatsResponse


class CategoryCountResponse(BaseModel):
    category: str
    count: int


class DailyPointResponse(BaseModel):
    date: str
    started_live: int
    started_control: int
    eligible48_live: int
    eligible48_control: int
    replied48_live: int
    replied48_control: int
    replies_live: int
    replies_control: int


class LateSendsResponse(BaseModel):
    count: int
    total_sends: int
    median_lag_minutes: int | None
    max_lag_minutes: int | None


class ContinueBucketResponse(BaseModel):
    count: int
    dialogs: int


class ContinuesResponse(BaseModel):
    dialogs: int
    escalated_after: int
    resolved_after: int
    max_per_dialog: int
    distribution: list[ContinueBucketResponse]


class HealthResponse(BaseModel):
    total: int
    pending: int
    stuck: int
    failed: int
    unclear: int
    median_decision_minutes: int | None
    tokens_in: int
    tokens_out: int
    calls: int


class ReplySummaryResponse(BaseModel):
    generated_at: str
    days: int
    gate_mode: str
    live_since: str | None
    effect: EffectResponse
    categories: list[CategoryCountResponse]
    categories_slices: list[CategoryCountResponse]
    categories_before: list[CategoryCountResponse]
    categories_after: list[CategoryCountResponse]
    actions: dict[str, int]
    daily: list[DailyPointResponse]
    escalations_open: int
    late_sends: LateSendsResponse
    continues: ContinuesResponse
    health: HealthResponse


class EscalationResponse(BaseModel):
    chat_id: str
    prompt_at: str
    nm_id: int | None
    category: str
    reason: str | None
    event_at: str
    state: str
    closed: bool
    excluded: bool
    chat_url: str | None


class EscalationListResponse(BaseModel):
    items: list[EscalationResponse]


class ReplyMessageResponse(BaseModel):
    at: str
    role: str
    text: str


class ReplyVerdictResponse(BaseModel):
    first_event_at: str
    last_event_at: str
    messages: int
    status: str
    category: str | None
    reason: str | None
    action: str


class ReplyDialogResponse(BaseModel):
    chat_id: str
    prompt_at: str
    chat_url: str | None
    messages: list[ReplyMessageResponse]
    verdicts: list[ReplyVerdictResponse]


class MonitorCheckResponse(BaseModel):
    id: str
    group: str
    title: str
    severity: str
    value: str | None
    threshold: str
    hint: str


class MonitorIncidentResponse(BaseModel):
    check_id: str
    title: str
    severity: str
    started_at: str
    ended_at: str | None
    value: str | None


class MonitorDayResponse(BaseModel):
    date: str
    warn_minutes: int
    crit_minutes: int


class MonitorResponse(BaseModel):
    checked_at: str
    worker_checked_at: str | None
    overall: str
    checks: list[MonitorCheckResponse]
    incidents: list[MonitorIncidentResponse]
    days: list[MonitorDayResponse]
