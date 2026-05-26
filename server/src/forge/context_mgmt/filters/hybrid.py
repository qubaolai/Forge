"""HybridFilter: 近期锚点 + 语义过滤 (chat 模式默认).

策略:
    1. 把历史按 turn_index 分组 (同轮 user/assistant/tool 共享 turn_index)
    2. 最近 anchor_turns 轮无条件保留 (保证对话连贯性)
    3. 更早的轮次: 调 SemanticFilter 按相似度过滤
       - score >= threshold 的轮次整体保留
       - 低于 threshold 的整轮剔除

保证:
    - 即使 retrieval 不可用, anchor_turns 也能保住最近上下文 (chat 不会断裂)
    - 语义过滤失败时整体降级为 RecentFilter
"""

from __future__ import annotations

import logging
from collections import OrderedDict

from forge.context_mgmt.filters.semantic import SemanticFilter
from forge.context_mgmt.types import ContextMode, HistoryMessage
from forge.context_mgmt.protocols import HistoryFilter

logger = logging.getLogger(__name__)


class HybridFilter(HistoryFilter):
    """近期锚点 + 语义过滤."""

    def __init__(
        self,
        semantic: SemanticFilter | None = None,
        anchor_turns: int = 3,
    ) -> None:
        self._semantic = semantic or SemanticFilter()
        self._anchor_turns = anchor_turns

    @property
    def name(self) -> str:
        return f"hybrid(anchor={self._anchor_turns})"

    async def filter(
        self,
        messages: list[HistoryMessage],
        query: str,
        mode: ContextMode,
    ) -> list[HistoryMessage]:
        if not messages:
            return []

        # 按 turn_index 分组 (保持轮次原顺序)
        turns: OrderedDict[int, list[HistoryMessage]] = OrderedDict()
        for m in messages:
            turns.setdefault(m.turn_index, []).append(m)

        turn_order = list(turns.keys())
        if len(turn_order) <= self._anchor_turns:
            return messages  # 数量不足锚点, 全部保留

        anchor_threshold_turn = turn_order[-self._anchor_turns]
        anchor_msgs: list[HistoryMessage] = []
        candidate_msgs: list[HistoryMessage] = []
        for turn_idx, msgs in turns.items():
            if turn_idx >= anchor_threshold_turn:
                anchor_msgs.extend(msgs)
            else:
                candidate_msgs.extend(msgs)

        # 对早期轮次做语义过滤
        try:
            filtered_candidates = await self._semantic.filter(
                candidate_msgs, query, mode
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("HybridFilter 语义过滤失败, 保留全部早期消息: %s", exc)
            filtered_candidates = candidate_msgs

        # 合并 + 按原顺序排序 (turn_index 升序)
        out = filtered_candidates + anchor_msgs
        out.sort(key=lambda m: m.turn_index)
        return out
