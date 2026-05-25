"""SemanticFilter: 按向量相似度过滤历史消息.

设计:
    - 依赖一个 RelevanceScorer Protocol (本文件内部定义), 接受 query + messages,
      返回每条消息的相似度得分.
    - score < min_score 的消息被剔除.
    - Scorer 失败时降级为 RecentFilter (保留全部), 并记到 degraded.

阶段 4 当前实现:
    - 提供 NullScorer (始终返回 1.0, 等价于不过滤) 作为安全兜底.
    - 后续接入 embedder 时, 实现 EmbeddingScorer 并替换默认值.
"""

from __future__ import annotations

import logging
from typing import Protocol

from forge.context_mgmt.types import ContextMode, HistoryMessage

logger = logging.getLogger(__name__)


class RelevanceScorer(Protocol):
    """计算 query 与每条 history 消息的相似度."""

    async def score(
        self,
        query: str,
        messages: list[HistoryMessage],
    ) -> list[float]:
        """返回长度与 messages 相同的得分列表 (0.0 ~ 1.0)."""
        ...


class NullScorer:
    """始终返回 1.0, 等价于不过滤 (阶段 4 默认, 后续替换为 EmbeddingScorer)."""

    async def score(
        self, query: str, messages: list[HistoryMessage]
    ) -> list[float]:
        return [1.0] * len(messages)


class SemanticFilter:
    """按相似度阈值过滤 history."""

    def __init__(
        self,
        scorer: RelevanceScorer | None = None,
        min_score: float = 0.6,
    ) -> None:
        self._scorer = scorer or NullScorer()
        self._min_score = min_score

    @property
    def name(self) -> str:
        return f"semantic(min={self._min_score:.2f})"

    async def filter(
        self,
        messages: list[HistoryMessage],
        query: str,
        mode: ContextMode,
    ) -> list[HistoryMessage]:
        if not messages:
            return []
        try:
            scores = await self._scorer.score(query, messages)
        except Exception as exc:  # noqa: BLE001
            logger.warning("SemanticFilter scorer 失败, 降级保留全部: %s", exc)
            return messages

        out: list[HistoryMessage] = []
        for msg, score in zip(messages, scores):
            msg.relevance_score = score
            if score >= self._min_score:
                out.append(msg)
        return out
