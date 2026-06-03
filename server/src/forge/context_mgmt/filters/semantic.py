"""SemanticFilter: 按向量相似度过滤历史消息.

设计:
    - 依赖一个 RelevanceScorer ABC (本文件内部定义), 接受 query + messages,
      返回每条消息的相似度得分.
    - score < min_score 的消息被剔除.
    - Scorer 失败时降级为 RecentFilter (保留全部), 并记到 degraded.

阶段 4 当前实现:
    - 提供 NullScorer (始终返回 1.0, 等价于不过滤) 作为安全兜底.
    - 后续接入 embedder 时, 实现 EmbeddingScorer 并替换默认值.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from forge.context_mgmt.protocols import HistoryFilter
from forge.context_mgmt.types import ContextMode, HistoryMessage
from forge.retrieval.embedders.base import Embedder

logger = logging.getLogger(__name__)


class RelevanceScorer(ABC):
    """计算 query 与每条 history 消息的相似度."""

    @abstractmethod
    async def score(
        self,
        query: str,
        messages: list[HistoryMessage],
    ) -> list[float]:
        """返回长度与 messages 相同的得分列表 (0.0 ~ 1.0)."""
        ...


class NullScorer(RelevanceScorer):
    """始终返回 1.0, 等价于不过滤 (阶段 4 默认, 后续替换为 EmbeddingScorer)."""

    async def score(
        self, query: str, messages: list[HistoryMessage]
    ) -> list[float]:
        return [1.0] * len(messages)

class EmbeddingScorer(RelevanceScorer):
    """向量相似度打分: 读缓存消息向量 + 对 query 实时 embedding, 算余弦相似度。

    设计 (修订 D):
        - 消息向量由冷路径任务 (context.embedding) 预算并缓存到 message_embeddings,
          这里只 batch_get 候选向量, 不再每轮对全部历史重算 (避免昂贵的批量 embedding)。
        - 缺缓存向量的消息 (冷启动 / 模型切换 / 维度不符) 给 1.0 分 (保留, 不误删)。
        - embed_query 是同步调用, 放 asyncio.to_thread 避免阻塞事件循环。
    """

    def __init__(
        self,
        embedder: Embedder,
        store: Any | None = None,
        *,
        model: str | None = None,
    ) -> None:
        self._embedder = embedder
        # MessageEmbeddingStore (含 async batch_get(ids, model=...)); None 时全部走保留兜底
        self._store = store
        self._model = model or getattr(embedder, "model_name", "") or ""

    async def score(
        self, query: str, messages: list[HistoryMessage]
    ) -> list[float]:
        if not messages:
            return []

        # 1. 批量读候选消息的缓存向量 (按当前 model 匹配)
        cached: dict[str, list[float]] = {}
        if self._store is not None:
            try:
                cached = await self._store.batch_get(
                    [m.id for m in messages], model=self._model
                )
            except Exception as exc:  # noqa: BLE001 — 缓存读失败, 早期轮次全部保留
                logger.warning("消息向量缓存读取失败, 保留全部: %s", exc)
                return [1.0] * len(messages)

        # 2. query 向量 (同步 embed 放线程池避免阻塞事件循环)
        import asyncio

        raw = await asyncio.to_thread(self._embedder.embed_query, query)
        query_vec = _as_vector(raw)

        # 3. 逐条算余弦; 无缓存向量 / 维度不符 -> 1.0 (保留, 不误删)
        scores: list[float] = []
        for m in messages:
            vec = cached.get(m.id)
            if not vec or len(vec) != len(query_vec):
                scores.append(1.0)
                continue
            scores.append(_cosine_similarity(query_vec, vec))
        return scores


def _as_vector(raw: Any) -> list[float]:
    """把 embed_query 返回值规整成单条向量 list[float] (兼容嵌套 [[...]] 返回)。"""
    if raw and isinstance(raw[0], (list, tuple)):
        return list(raw[0])
    return list(raw or [])


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """纯 python 余弦相似度 (避免热路径硬依赖 numpy)。"""
    import math

    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class SemanticFilter(HistoryFilter):
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
        for msg, score in zip(messages, scores, strict=False):
            msg.relevance_score = score
            if score >= self._min_score:
                out.append(msg)
        return out
