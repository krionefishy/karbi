from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)


class UserResponse(BaseModel):
    id: str
    username: str
    is_admin: bool


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserResponse


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=8, max_length=1024)


class EmployeeResponse(BaseModel):
    id: str
    username: str
    is_active: bool
    is_admin: bool
    created_at: datetime
    last_login_at: datetime | None


class EmployeeCreate(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    is_admin: bool = False


class EmployeeUpdate(BaseModel):
    is_active: bool | None = None
    is_admin: bool | None = None


class IssuedPasswordResponse(BaseModel):
    """The generated password travels to the administrator exactly once."""

    user: EmployeeResponse
    password: str
