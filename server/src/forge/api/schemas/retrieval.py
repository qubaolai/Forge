"""检索调试接口的 Pydantic 契约。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_n: int | None = Field(default=None, ge=1, le=50)
    doc_id_filter: list[str] | None = None


class SearchItem(BaseModel):
    chunk_id: str
    doc_id: str
    header_path: str
    content: str
    final_score: float
    fusion_score: float
    rerank_score: float | None = None
    hit_child_count: int = 0
    hit_chunk_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    query: str
    items: list[SearchItem] = Field(default_factory=list)
