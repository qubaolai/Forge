"""管理员后台用户管理 schema。"""

from pydantic import BaseModel, EmailStr, Field


class UserCreateIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    role: str = "member"  # owner / admin / member / guest
    status: str = "active"
    avatar_url: str | None = None


class UserUpdateIn(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=64)
    role: str | None = None
    status: str | None = None
    avatar_url: str | None = None
