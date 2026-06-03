"""检索管道配置: 通用组件模式 / Ingest / BM25 / Recall / Fusion / Rerank."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


# ======================================================================
# 通用: ComponentConfig — provider + providers 模式
# ======================================================================
class ComponentConfig(BaseModel):
    """组件配置: provider 选定 + providers 子配置表.

    适用于 embedding / vector_store / reranker 等启动时固定的组件.
    工厂调用: Factory.create(cfg.provider, cfg.active_config())
    """
    model_config = {"extra": "forbid"}

    provider: str
    providers: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_active_present(self) -> ComponentConfig:
        if self.provider not in self.providers:
            raise ValueError(
                f"provider={self.provider!r} 在 providers 中没有对应的子配置, "
                f"已配置: {sorted(self.providers.keys())}"
            )
        return self

    def active_config(self) -> dict[str, Any]:
        return self.providers[self.provider]

    def available_providers(self) -> list[str]:
        return sorted(self.providers.keys())


# ======================================================================
# Ingest: 文档入库
# ======================================================================
class ChunkingConfig(BaseModel):
    model_config = {"extra": "forbid"}

    strategy: str = "hierarchical"
    chunk_size: int = 500
    chunk_overlap: int = 50

    @field_validator("strategy")
    @classmethod
    def _check_strategy(cls, v: str) -> str:
        allowed = {"hierarchical", "sliding_window"}
        if v not in allowed:
            raise ValueError(f"chunking.strategy 必须是 {allowed} 之一")
        return v

    @model_validator(mode="after")
    def _check_overlap(self) -> ChunkingConfig:
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap 必须小于 chunk_size")
        return self


class IngestConfig(BaseModel):
    model_config = {"extra": "forbid"}

    documents_path: str = "data/documents"
    chunking: ChunkingConfig = ChunkingConfig()


# ======================================================================
# BM25: 倒排索引 (带分词器横切段)
# ======================================================================
class TokenizerConfig(BaseModel):
    model_config = {"extra": "forbid"}

    type: str = "jieba"
    user_dict: str | None = None


class BM25StoreConfig(BaseModel):
    model_config = {"extra": "forbid"}

    provider: str
    providers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    tokenizer: TokenizerConfig = TokenizerConfig()

    @model_validator(mode="after")
    def _check_active_present(self) -> BM25StoreConfig:
        if self.provider not in self.providers:
            raise ValueError(
                f"bm25_store.provider={self.provider!r} 在 providers 中没有对应子配置"
            )
        return self

    def active_config(self) -> dict[str, Any]:
        return self.providers[self.provider]


# ======================================================================
# Retrieval: 检索流程控制
# ======================================================================
class RecallChannelConfig(BaseModel):
    model_config = {"extra": "forbid"}

    enabled: bool = True
    top_k: int = 30


class RecallConfig(BaseModel):
    model_config = {"extra": "forbid"}

    vector: RecallChannelConfig = RecallChannelConfig()
    bm25: RecallChannelConfig = RecallChannelConfig()

    @model_validator(mode="after")
    def _check_at_least_one_enabled(self) -> RecallConfig:
        if not (self.vector.enabled or self.bm25.enabled):
            raise ValueError("recall.vector 与 recall.bm25 至少要启用一个")
        return self


class FusionConfig(BaseModel):
    model_config = {"extra": "forbid"}

    strategy: str = "rrf"
    rrf_k: int = 60
    weighted_vector: float = 0.7
    weighted_bm25: float = 0.3

    @field_validator("strategy")
    @classmethod
    def _check_strategy(cls, v: str) -> str:
        allowed = {"rrf", "weighted"}
        if v not in allowed:
            raise ValueError(f"fusion.strategy 必须是 {allowed} 之一")
        return v


class AggregationConfig(BaseModel):
    model_config = {"extra": "forbid"}

    score_agg: str = "max"

    @field_validator("score_agg")
    @classmethod
    def _check_agg(cls, v: str) -> str:
        allowed = {"max", "sum", "mean"}
        if v not in allowed:
            raise ValueError(f"aggregation.score_agg 必须是 {allowed} 之一")
        return v


class RerankStageConfig(BaseModel):
    model_config = {"extra": "forbid"}

    enabled: bool = True


class RetrievalConfig(BaseModel):
    model_config = {"extra": "forbid"}

    top_n_parent: int = 5
    top_m_for_rerank: int = 20

    recall: RecallConfig = RecallConfig()
    fusion: FusionConfig = FusionConfig()
    aggregation: AggregationConfig = AggregationConfig()
    rerank: RerankStageConfig = RerankStageConfig()
