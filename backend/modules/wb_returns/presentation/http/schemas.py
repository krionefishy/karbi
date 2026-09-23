from pydantic import BaseModel


class ReturnResponse(BaseModel):
    shk_id: int
    sticker_id: str
    srid: str
    order_id: int
    nm_id: int
    barcode: str
    title: str
    brand: str
    subject_name: str
    tech_size: str
    return_type: str
    reason: str
    status: str
    status_key: str
    status_title: str
    dst_office_id: int | None
    dst_office_address: str
    order_dt: str | None
    status_changed_at: str
    ready_at: str | None
    free_until: str | None
    pickup_deadline: str | None


class ClaimResponse(BaseModel):
    id: str
    nm_id: int
    name: str
    user_comment: str
    wb_comment: str
    status: int
    status_ex: int
    is_archive: bool
    price: float
    currency_code: str
    srid: str
    photos: list[str]
    videos: list[str]
    actions: list[str]
    created_at: str
    order_dt: str | None
    delivery_dt: str | None
    review_deadline: str


class ReturnsResponse(BaseModel):
    seller_id: str
    seller_name: str
    collected_at: str | None
    collection_error: str | None
    ready: list[ReturnResponse]
    transit: list[ReturnResponse]
    other_active: list[ReturnResponse]
    history: list[ReturnResponse]
    claims: list[ClaimResponse]
    claims_history: list[ClaimResponse]


class InviteLinkResponse(BaseModel):
    url: str
    expires_at: str


class RefreshResponse(BaseModel):
    status: str
    in_progress: bool
    requested_at: str
    finished_at: str | None
    error: str | None


class ExtensionInstallResponse(BaseModel):
    id: str
    install_id: str
    browser: str
    created_at: str
    last_seen_at: str | None
    state: str
    last_error: str | None
    last_code_at: str | None
    deliveries_count: int
    deliveries_at: str | None


class ExtensionResponse(BaseModel):
    seller_id: str
    download_url: str
    code_date: str
    has_code_today: bool
    code_received_at: str | None
    pending_tasks: bool
    installs: list[ExtensionInstallResponse]


class PairingCodeResponse(BaseModel):
    code: str
    expires_at: str
