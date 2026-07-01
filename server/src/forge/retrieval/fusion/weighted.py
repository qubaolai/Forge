"""加权融合 (Weighted Fusion).

流程:
    1. 每路内部 min-max 归一化 score 到 [0, 1]
    2. 各路加权求和: fusion_score = Σ w_i * norm_score_i(d)
    3. 缺失路按 0 计

权重处理:
    - 配置 weights: {vector: 0.7, bm25: 0.3}, 字段名对齐 source name
    - 内部自动归一化权重和为 1, 用户写 0.6/0.4 或 7/3 都对
    - 配置中没出现的 source 默认权重 0 (不参与融合, 但仍会进结果集)

边界:
    - 某路只有一个 hit -> max == min, 归一化分裂为 0/0;
      退化为该 hit 归一化分 = 1.0 (它是该路唯一的最相关结果)
    - 某路所有 score 相等 -> 同上, 全部归一化为 1.0
"""

from __future__ import annotations

import logging

from ..recall.base import ChildHit
from .base import FusedHit, Fusion
from .factory import register_fusion

logger = logging.getLogger(__name__)


@register_fusion("weighted")
class WeightedFusion(Fusion):
    """加权融合 (min-max 归一化)."""

    def __init__(self, config: dict):
        super().__init__(config)
        raw_weights: dict = config.get("weights") or {}
        if not raw_weights:
            raise ValueError("WeightedFusion 需要 config['weights'] (非空 dict)")

        # 用户权重归一化: 任意非负值 -> 和为 1
        weights = {k: float(v) for k, v in raw_weights.items()}
        for k, v in weights.items():
            if v < 0:
                raise ValueError(f"权重不能为负: {k}={v}")
        total = sum(weights.values())
        if total <= 0:
            raise ValueError("权重总和必须 > 0")
        self.weights: dict[str, float] = {k: v / total for k, v in weights.items()}

    @property
    def name(self) -> str:
        return "weighted"

    def fuse(
        self,
        hits_per_source: dict[str, list[ChildHit]],
    ) -> list[FusedHit]:
        if not hits_per_source:
            return []

        # 1. 每路先按 chunk_id 去重, 再 min-max 归一化
        deduped_hits_per_source = {
            source: self._dedupe_source_hits(hits)
            for source, hits in hits_per_source.items()
        }
        norm_per_source: dict[str, dict[str, float]] = {}
        for source, hits in deduped_hits_per_source.items():
            norm_per_source[source] = self._normalize(hits)

        # 2. 按 chunk_id 累计加权
        accum: dict[str, FusedHit] = {}
        for source, hits in deduped_hits_per_source.items():
            w = self.weights.get(source, 0.0)
            norm_map = norm_per_source[source]
            if w == 0.0:
                logger.debug(
                    "WeightedFusion: source=%r 权重为 0, 仅参与命中标记不计入分数",
                    source,
                )
            for hit in hits:
                contrib = w * norm_map[hit.chunk_id]
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
                    if source not in existing.sources:
                        existing.sources.append(source)
                    prev = existing.rank_per_source.get(source)
                    if prev is None or hit.rank < prev:
                        existing.rank_per_source[source] = hit.rank

        result = sorted(accum.values(), key=lambda f: f.fusion_score, reverse=True)
        logger.debug(
            "WeightedFusion: weights=%s 输入 %d 路 (sizes=%s) 输出 %d 个唯一 chunk",
            self.weights,
            len(hits_per_source),
            {s: len(h) for s, h in hits_per_source.items()},
            len(result),
        )
        return result

    @staticmethod
    def _dedupe_source_hits(hits: list[ChildHit]) -> list[ChildHit]:
        """同一路召回内同一 chunk 只贡献一次, 取分数最高的命中."""
        by_chunk: dict[str, ChildHit] = {}
        for hit in hits:
            existing = by_chunk.get(hit.chunk_id)
            if existing is None or hit.score > existing.score:
                by_chunk[hit.chunk_id] = hit
            elif hit.score == existing.score and hit.rank < existing.rank:
                by_chunk[hit.chunk_id] = hit
        return sorted(by_chunk.values(), key=lambda h: h.rank)

    @staticmethod
    def _normalize(hits: list[ChildHit]) -> dict[str, float]:
        """Min-max 归一化某一路的 hits, 返回 {chunk_id: norm_score}.

        边界:
            - 空列表       -> 空 dict
            - max == min   -> 全部赋 1.0 (该路所有命中等权)
        """
        if not hits:
            return {}
        scores = [h.score for h in hits]
        s_min = min(scores)
        s_max = max(scores)
        rng = s_max - s_min
        if rng == 0:
            # 所有分相等: 不能区分, 全部赋 1.0
            return {h.chunk_id: 1.0 for h in hits}
        return {h.chunk_id: (h.score - s_min) / rng for h in hits}
