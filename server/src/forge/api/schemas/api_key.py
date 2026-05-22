"""API Key 管理相关 schema。"""

from datetime import datetime

from pydantic import BaseModel, Field


class ApiKeyCreateIn(BaseModel):
    """创建 API Key 的请求体。"""

    name: str = Field(min_length=1, max_length=128, description="Key 名称标签，如 '我的 MacBook CLI'")


class ApiKeyOut(BaseModel):
    """API Key 创建响应 — 仅此时返回原始 key 明文。"""

    id: str
    name: str
    prefix: str
    raw_key: str = Field(description="原始 Key 明文，仅返回一次，请妥善保存")
    created_at: datetime


class ApiKeyListItem(BaseModel):
    """API Key 列表项 — 不包含原始 key。"""

    id: str
    name: str
    prefix: str
    last_used_at: datetime | None
    expires_at: datetime | None
    is_revoked: bool
    created_at: datetime
