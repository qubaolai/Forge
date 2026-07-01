"""KB 相关 Pydantic 契约 (请求 / 响应 schema)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


# ----------------------------------------------------------------------
# 请求体
# ----------------------------------------------------------------------
class KbCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    visibility: Literal["private", "workspace", "public"] = "private"
    chunk_size: int = Field(default=512, ge=64, le=4096)
    chunk_overlap: int = Field(default=64, ge=0, le=512)

    @model_validator(mode="after")
    def _check_chunk_overlap(self) -> KbCreateIn:
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap 必须小于 chunk_size")
        return self


class KbUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    visibility: Literal["private", "workspace", "public"] | None = None
    chunk_size: int | None = Field(default=None, ge=64, le=4096)
    chunk_overlap: int | None = Field(default=None, ge=0, le=512)

    @model_validator(mode="after")
    def _check_chunk_overlap(self) -> KbUpdateIn:
        if (
            self.chunk_size is not None
            and self.chunk_overlap is not None
            and self.chunk_overlap >= self.chunk_size
        ):
            raise ValueError("chunk_overlap 必须小于 chunk_size")
        return self


# ----------------------------------------------------------------------
# 响应体
# ----------------------------------------------------------------------
class KbInfo(BaseModel):
    id: str
    name: str
    description: str | None
    visibility: str
    owner_id: str
    collaborators: list = Field(default_factory=list)
    chunk_size: int
    chunk_overlap: int
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
    embedding_model_id: str | None
    vector_index_status: str
    vector_index_error: str | None
    vector_indexed_at: datetime | None
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


class KbDocumentUploadOut(BaseModel):
    """文档上传返回 (异步入库, 仅回执基础信息)."""

    id: str
    name: str
    status: str


class KbChildChunkDebugInfo(BaseModel):
    id: str
    source_type: str = "text"
    chars: int = 0
    content_preview: str = ""
    splitter: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    table_index: int | None = None


class KbDocumentChunkInfo(BaseModel):
    chunk_id: str
    seq: int
    header_path: str = ""
    source_type: str = "text"
    page: int | None = None
    page_start: int | None = None
    page_end: int | None = None
    token_count: int = 0
    content_chars: int = 0
    content_preview: str = ""
    metadata: dict = Field(default_factory=dict)
    child_count: int = 0
    child_debug_manifest: list[KbChildChunkDebugInfo] = Field(default_factory=list)


class KbDocumentChunkListResponse(BaseModel):
    items: list[KbDocumentChunkInfo]
    total: int
    page: int
    page_size: int


# ----------------------------------------------------------------------
# 检索测试
# ----------------------------------------------------------------------
class KbSearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=2048)
    top_n: int = Field(default=5, ge=1, le=20)
    debug: bool = False


class KbSearchHit(BaseModel):
    """单条检索命中片段 (面向 KB 详情页检索测试)."""

    chunk_id: str
    document_id: str
    document_name: str
    kb_name: str
    content: str
    score: float
    page: int | None = None
    page_start: int | None = None
    page_end: int | None = None
    header_path: str = ""
    source_url: str | None = None


class KbRetrievalRecallHit(BaseModel):
    rank: int
    chunk_id: str
    parent_id: str
    document_id: str
    score: float


class KbRetrievalFusionHit(BaseModel):
    chunk_id: str
    parent_id: str
    document_id: str
    fusion_score: float
    sources: list[str] = Field(default_factory=list)
    rank_per_source: dict[str, int] = Field(default_factory=dict)


class KbRetrievalAggregationHit(BaseModel):
    parent_id: str
    document_id: str
    fusion_score: float
    hit_child_count: int
    hit_chunk_ids: list[str] = Field(default_factory=list)


class KbRetrievalRerankHit(BaseModel):
    parent_id: str
    before_rank: int
    after_rank: int
    fusion_score: float
    rerank_score: float | None = None
    final_score: float


class KbRetrievalTrace(BaseModel):
    recall: dict[str, list[KbRetrievalRecallHit]] = Field(default_factory=dict)
    fusion: list[KbRetrievalFusionHit] = Field(default_factory=list)
    aggregation: list[KbRetrievalAggregationHit] = Field(default_factory=list)
    rerank: list[KbRetrievalRerankHit] = Field(default_factory=list)


class KbSearchResponse(BaseModel):
    items: list[KbSearchHit]
    total: int
    query: str
    trace: KbRetrievalTrace | None = None


class KbChunkFullText(BaseModel):
    chunk_id: str
    document_id: str
    document_name: str
    kb_id: str
    kb_name: str
    content: str
    header_path: str = ""
    source_type: str = "text"
    metadata: dict = Field(default_factory=dict)
    page: int | None = None
    page_start: int | None = None
    page_end: int | None = None
    source_url: str | None = None


# ----------------------------------------------------------------------
# Citation: chat 回答的结构化引用来源 (落 chat_messages.citations + SSE 下发)
# ----------------------------------------------------------------------
class Citation(BaseModel):
    """检索来源引用. 字段与前端 web/src/types Citation 对齐.

    index 即正文里的 [N] 角标序号; metadata 容纳 kb_name / page /
    header_path / source_url 等可选展示信息.
    """

    index: int
    chunk_id: str
    document_id: str
    document_name: str
    content: str
    score: float
    metadata: dict = Field(default_factory=dict)
