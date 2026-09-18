from pydantic import BaseModel


class GroupSummaryResponse(BaseModel):
    total: int
    replied: int
    silent: int
    pending: int
    # Доли от диалогов с известным исходом; null — таких ещё нет.
    reply_rate: float | None
    silent_rate: float | None


class DaySummaryResponse(BaseModel):
    day: str
    followed: GroupSummaryResponse
    bare: GroupSummaryResponse


class DialogResponse(BaseModel):
    chat_id: str
    prompt_at: str
    nm_id: int | None
    product_name: str
    group: str
    outcome: str
    follow_up_at: str | None
    follow_up_text: str | None
    follow_up_late: bool
    reply_at: str | None
    reply_text: str | None
    reply_has_attachments: bool


class ReviewChatsResponse(BaseModel):
    seller_id: str
    seller_name: str
    date_from: str
    date_to: str
    reply_window_hours: int
    followed: GroupSummaryResponse
    bare: GroupSummaryResponse
    follow_up_late: int
    first_follow_up_at: str | None
    days: list[DaySummaryResponse]
    dialogs: list[DialogResponse]
    page: int
    page_size: int
    total_dialogs: int
    history_from: str | None
    synced_through: str | None
    collection_error: str | None
