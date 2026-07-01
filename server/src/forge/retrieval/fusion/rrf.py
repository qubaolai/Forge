"""Reciprocal Rank Fusion (RRF) 融合.

公式:
    score(d) = Σ_i  1 / (k + rank_i(d))

    rank_i(d): 文档 d 在第 i 路的 1-based rank, 不在第 i 路则该项为 0
    k:         平滑参数, 经典取 60, k 越大排名差异越平滑

特性:
    - 无需归一化, 直接按 rank 计算, 对量纲差异天然鲁棒
    - 一路命中也算命中 (该路得分 = 1/(k+rank))
    - 跨路命中得分自然累加, 多模态确认的 chunk 排名靠前

输出:
    - score 量纲约 [0, 0.1] 区间, 数值绝对值无意义, 排序意义有意义
    - 返回全部融合后子块, 不做 top_k 截断
"""

from __future__ import annotations

import logging

from ..recall.base import ChildHit
from .base import FusedHit, Fusion
from .factory import register_fusion

logger = logging.getLogger(__name__)


@register_fusion("rrf")
class RRFFusion(Fusion):
    """Reciprocal Rank Fusion."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.k = int(config.get("rrf_k", 60))
        if self.k <= 0:
            raise ValueError(f"rrf_k 必须 > 0, 收到 {self.k}")

    @property
    def name(self) -> str:
        return "rrf"

    def fuse(
        self,
        hits_per_source: dict[str, list[ChildHit]],
    ) -> list[FusedHit]:
        if not hits_per_source:
            return []

        # chunk_id -> 累计的 FusedHit (用 dict 累加)
        accum: dict[str, FusedHit] = {}

        for source, hits in hits_per_source.items():
            for hit in self._dedupe_source_hits(hits):
                contrib = 1.0 / (self.k + hit.rank)
                existing = accum.get(hit.chunk_id)
                if existing is None:
                    accum[hit.chunk_id] = FusedHit(
                        chunk_id=hit.chunk_id,
                        parent_id=hit.parent_id,
                        doc_id=hit.doc_id,
                        fusion_score=contrib,
                        sources=[source],
                        rank_per_source={source: hit.rank},
                    )
                else:
                    existing.fusion_score += contrib
                    # sources 有序去重: 同一路重复出现 (理论上不该, 防御性) 不重加
                    if source not in existing.sources:
                        existing.sources.append(source)
                    # 同一路重复 chunk_id 时取更靠前的 rank (更小)
                    prev = existing.rank_per_source.get(source)
                    if prev is None or hit.rank < prev:
                        existing.rank_per_source[source] = hit.rank

        result = sorted(accum.values(), key=lambda f: f.fusion_score, reverse=True)
        logger.debug(
            "RRF: k=%d 输入 %d 路 (sizes=%s) 输出 %d 个唯一 chunk",
            self.k,
            len(hits_per_source),
            {s: len(h) for s, h in hits_per_source.items()},
            len(result),
        )
        return result

    @staticmethod
    def _dedupe_source_hits(hits: list[ChildHit]) -> list[ChildHit]:
        """同一路召回内同一 chunk 只贡献一次, 取 rank 最靠前的命中."""
        by_chunk: dict[str, ChildHit] = {}
        for hit in hits:
            existing = by_chunk.get(hit.chunk_id)
            if existing is None or hit.rank < existing.rank:
                by_chunk[hit.chunk_id] = hit
            elif hit.rank == existing.rank and hit.score > existing.score:
                by_chunk[hit.chunk_id] = hit
        return sorted(by_chunk.values(), key=lambda h: h.rank)
