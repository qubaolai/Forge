"""认证与用户相关的 schema。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


# ---- 输出 ----
class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str = Field(validation_alias="user_id")
    email: EmailStr
    name: str
    avatar_url: str | None = None
    role: str
    status: str
    created_at: datetime
    updated_at: datetime


class LoginOut(BaseModel):
    access_token: str
    expires_at: datetime
    user: UserOut


class RefreshOut(BaseModel):
    access_token: str
    expires_at: datetime


# ---- 输入 ----
class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6, max_length=128)
