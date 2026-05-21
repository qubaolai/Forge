"""通用 schema:分页、错误响应等。"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageQuery(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=200)


class ApiError(BaseModel):
    """统一异常响应体的 data 字段(由 middleware 包装)。"""

    code: int
    msg: str
    detail: dict | list | None = None
