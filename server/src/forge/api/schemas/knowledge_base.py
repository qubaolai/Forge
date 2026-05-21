"""KB 相关 Pydantic 契约 (请求 / 响应 schema)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# ----------------------------------------------------------------------
# 请求体
# ----------------------------------------------------------------------
class KbCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    visibility: Literal["private", "workspace", "public"] = "private"
    chunk_size: int = Field(default=512, ge=64, le=4096)
    chunk_overlap: int = Field(default=64, ge=0, le=512)


class KbUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    visibility: Literal["private", "workspace", "public"] | None = None


# ----------------------------------------------------------------------
# 响应体
# ----------------------------------------------------------------------
class KbInfo(BaseModel):
    id: str
    name: str
    description: str | None
    visibility: str
    embedding_model: str | None
    document_count: int
    chunk_count: int
    size_bytes: int
    created_at: datetime
    updated_at: datetime


class KbDocumentInfo(BaseModel):
    id: str
    kb_id: str
    name: str
    source: str
    source_url: str | None
    mime_type: str
    size_bytes: int
    content_hash: str | None
    status: str
    status_message: str | None
    progress: int
    chunk_count: int
    indexed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class KbListResponse(BaseModel):
    items: list[KbInfo]
    total: int


class KbDocumentListResponse(BaseModel):
    items: list[KbDocumentInfo]
    total: int
    page: int
    page_size: int
