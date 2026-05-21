"""融合层与父块聚合抽象.

流程:
    多路 ChildHit
        → Fusion.fuse           子块层融合, 输出 FusedHit (按融合分降序)
        → Aggregator.aggregate  按 parent_id 聚合, 输出 AggregatedParent
        → 上层 pipeline 截 top_m, 回查父块原文, 送 rerank

分层原则:
    - fusion 与 aggregator 是正交的两个策略维度, 各自独立工厂
    - 两者只做数据变换, 不做截断 (上层 pipeline 负责 top_m)
    - 两者不感知存储层 (parent_store), 不回查原文
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..recall.base import ChildHit  # noqa: F401  (供子模块复用类型)


@dataclass
class FusedHit:
    """子块层融合输出.

    fusion_score 一律 "越大越相关". 不同 Fusion 实现的量纲不同:
        - RRF:      Σ 1/(k+rank), 通常 < 0.1
        - Weighted: 各路归一化分加权, 通常 ∈ [0, 1]
    跨 Fusion 实现的 score 不可比, 但同一次调用内排序有意义.
    """

    chunk_id: str
    parent_id: str
    doc_id: str
    fusion_score: float

    # 命中该子块的所有路名 (有序去重, 按首次出现顺序)
    sources: list[str] = field(default_factory=list)

    # 各路原始 rank, 用于调试 / 可观测性
    # {'vector': 3, 'bm25': 7} 表示 vector 路第 3, bm25 路第 7
    rank_per_source: dict[str, int] = field(default_factory=dict)


@dataclass
class AggregatedParent:
    """父块聚合输出.

    本 DTO 不含父块原文. 原文回查由上层 retrieval pipeline 通过
    parent_store.get_many(parent_ids) 完成. 聚合层不依赖存储层.

    hit_chunk_ids 按子块的 fusion_score 降序排列, 便于:
        - 调试时一眼看出哪个子块贡献最大
        - 后续若改为 "子块级 rerank" 时能直接拿排好序的子块列表
    """

    parent_id: str
    doc_id: str
    fusion_score: float
    hit_child_count: int
    hit_chunk_ids: list[str]


# ----------------------------------------------------------------------
# Fusion 抽象
# ----------------------------------------------------------------------
class Fusion(ABC):
    """多路召回融合抽象."""

    def __init__(self, config: dict):
        self.config = config

    @property
    @abstractmethod
    def name(self) -> str:
        """策略名, 用于日志."""

    @abstractmethod
    def fuse(
        self,
        hits_per_source: dict[str, list[ChildHit]],
    ) -> list[FusedHit]:
        """融合多路召回结果.

        Args:
            hits_per_source: {source_name: list[ChildHit]}
                每路 hits 已按 rank 升序 (rank=1 最相关).
                ChildHit.source 应与外层 dict key 一致, 实现层不强校验.

        Returns:
            按 fusion_score 降序排列的 FusedHit 列表.
            同一 chunk_id 多路命中合并为一条.
            不做 top_k 截断, 返回全部融合后子块.
        """


# ----------------------------------------------------------------------
# Aggregator 抽象
# ----------------------------------------------------------------------
class Aggregator(ABC):
    """父块聚合抽象. 把子块层 FusedHit 聚合到父块粒度."""

    def __init__(self, config: dict):
        self.config = config

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def aggregate(
        self,
        fused_hits: list[FusedHit],
    ) -> list[AggregatedParent]:
        """按 parent_id 分组, 用策略算父块 fusion_score.

        Returns:
            按父块 fusion_score 降序排列的 AggregatedParent 列表.
            不做 top_m 截断 (上层 pipeline 负责).
            空 parent_id 的 hit 会被跳过, 记 WARNING.
        """
