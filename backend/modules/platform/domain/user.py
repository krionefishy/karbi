import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class User:
    id: uuid.UUID
    username: str
    password_hash: str
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None
