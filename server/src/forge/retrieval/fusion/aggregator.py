"""父块聚合实现: max / sum / mean.

策略对比:
    max:    parent_score = max(child_scores)
            稳定, 不受父块大小影响, 推荐默认
    sum:    parent_score = sum(child_scores)
            奖励命中密集的父块, 但偏向大父块 (子块越多累加越高)
    mean:   parent_score = mean(child_scores)
            介于两者之间, 平均匹配度

行为约定:
    - 输入按子块 fusion_score 已经/未必有序, 不依赖输入顺序
    - 输出 hit_chunk_ids 按子块 fusion_score 降序
    - 父块 fusion_score 降序排列
    - 空 parent_id 跳过, 记 WARNING (数据 bug 兜底, 不阻塞流程)
"""

from __future__ import annotations

import logging

from .base import AggregatedParent, Aggregator, FusedHit
from .factory import register_aggregator

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# 共享分组逻辑
# ----------------------------------------------------------------------
def _group_by_parent(
    fused_hits: list[FusedHit],
) -> dict[str, list[FusedHit]]:
    """按 parent_id 分组. 跳过空 parent_id, 记 WARNING."""
    groups: dict[str, list[FusedHit]] = {}
    skipped = 0
    for h in fused_hits:
        if not h.parent_id:
            skipped += 1
            continue
        groups.setdefault(h.parent_id, []).append(h)
    if skipped > 0:
        logger.warning(
            "Aggregator: 跳过 %d 个 parent_id 为空的 hit (数据问题, 检查入库链路)",
            skipped,
        )
    return groups


def _build_parent(
    parent_id: str,
    children: list[FusedHit],
    parent_score: float,
) -> AggregatedParent:
    """构造 AggregatedParent. children 按 fusion_score 降序排."""
    sorted_children = sorted(children, key=lambda c: c.fusion_score, reverse=True)
    # 用第一个子块的 doc_id 代表父块的 doc_id (同一父块的所有子块 doc_id 必然相同)
    return AggregatedParent(
        parent_id=parent_id,
        doc_id=sorted_children[0].doc_id,
        fusion_score=parent_score,
        hit_child_count=len(sorted_children),
        hit_chunk_ids=[c.chunk_id for c in sorted_children],
    )


def _sort_parents(parents: list[AggregatedParent]) -> list[AggregatedParent]:
    return sorted(parents, key=lambda p: p.fusion_score, reverse=True)


# ----------------------------------------------------------------------
# Max
# ----------------------------------------------------------------------
@register_aggregator("max")
class MaxAggregator(Aggregator):
    """父块分 = 子块分最大值. 推荐默认."""

    @property
    def name(self) -> str:
        return "max"

    def aggregate(self, fused_hits: list[FusedHit]) -> list[AggregatedParent]:
        groups = _group_by_parent(fused_hits)
        parents = [
            _build_parent(pid, children, max(c.fusion_score for c in children))
            for pid, children in groups.items()
        ]
        return _sort_parents(parents)


# ----------------------------------------------------------------------
# Sum
# ----------------------------------------------------------------------
@register_aggregator("sum")
class SumAggregator(Aggregator):
    """父块分 = 子块分累加. 偏向大父块, 注意.

    适用场景: 父块大小相对均匀, 想奖励命中密集度.
    """

    @property
    def name(self) -> str:
        return "sum"

    def aggregate(self, fused_hits: list[FusedHit]) -> list[AggregatedParent]:
        groups = _group_by_parent(fused_hits)
        parents = [
            _build_parent(pid, children, sum(c.fusion_score for c in children))
            for pid, children in groups.items()
        ]
        return _sort_parents(parents)


# ----------------------------------------------------------------------
# Mean
# ----------------------------------------------------------------------
@register_aggregator("mean")
class MeanAggregator(Aggregator):
    """父块分 = 子块分平均. 介于 max 和 sum 之间."""

    @property
    def name(self) -> str:
        return "mean"

    def aggregate(self, fused_hits: list[FusedHit]) -> list[AggregatedParent]:
        groups = _group_by_parent(fused_hits)
        parents = []
        for pid, children in groups.items():
            mean_score = sum(c.fusion_score for c in children) / len(children)
            parents.append(_build_parent(pid, children, mean_score))
        return _sort_parents(parents)
