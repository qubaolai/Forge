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
    # Excel 行级子块每块行数 (1=行级, 按值检索最精准; >1 分组降成本)
    excel_child_rows: int = 1

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

    @model_validator(mode="after")
    def _check_top_k_when_enabled(self) -> RecallChannelConfig:
        if self.enabled and self.top_k <= 0:
            raise ValueError("启用的 recall 通道 top_k 必须 > 0")
        return self


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


class HydeConfig(BaseModel):
    """HyDE (假想文档) 查询扩展. 仅作用于向量召回, 默认关闭.

    开启后每次向量检索前多一次 LLM 调用 (fast 档 + 网关缓存), 用生成的
    假想答案代替/拼接原始 query 去 embed, 提升稠密召回命中率; 失败自动
    降级为原始 query.
    """

    model_config = {"extra": "forbid"}

    enabled: bool = False
    max_tokens: int = 200
    # 把原始 query 拼到假想文档后一起 embed, 保留查询锚定, 更稳健
    concat_original: bool = True


class RetrievalConfig(BaseModel):
    model_config = {"extra": "forbid"}

    top_n_parent: int = 5
    top_m_for_rerank: int = 20

    # 回灌给 LLM 的 token 预算 (父块回灌框架用):
    #   recall_context_max_tokens: 单次检索所有片段合计上限
    #   snippet_min_tokens:        每条片段保底预算, 不足以给所有片段保底时按分数丢尾部
    recall_context_max_tokens: int = 6000
    snippet_min_tokens: int = 400

    recall: RecallConfig = RecallConfig()
    fusion: FusionConfig = FusionConfig()
    aggregation: AggregationConfig = AggregationConfig()
    rerank: RerankStageConfig = RerankStageConfig()
    hyde: HydeConfig = HydeConfig()

    @model_validator(mode="after")
    def _check_budget(self) -> RetrievalConfig:
        if self.snippet_min_tokens <= 0:
            raise ValueError("snippet_min_tokens 必须 > 0")
        if self.recall_context_max_tokens < self.snippet_min_tokens:
            raise ValueError(
                "recall_context_max_tokens 必须 >= snippet_min_tokens"
            )
        return self
