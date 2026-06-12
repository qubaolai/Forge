"""HybridFilter: 近期锚点 + 语义过滤 (chat 模式默认).

策略:
    1. 把历史按 turn_index 分组 (同轮 user/assistant/tool 共享 turn_index)
    2. 最近 anchor_turns 轮无条件保留 (保证对话连贯性)
    3. 更早的轮次: 可选调用 EmbeddingScorer 按相似度过滤
       - score >= threshold 的轮次整体保留
       - 低于 threshold 的整轮剔除

保证:
    - 即使 retrieval 不可用, anchor_turns 也能保住最近上下文 (chat 不会断裂)
    - 未配置 scorer 或语义打分失败时保留全部早期消息
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from typing import Any

from forge.context_mgmt.protocols import HistoryFilter
from forge.context_mgmt.types import ContextMode, HistoryMessage
from forge.retrieval.embedders.base import Embedder

logger = logging.getLogger(__name__)


class EmbeddingScorer:
    """读缓存消息向量，并对当前 query 实时向量化后计算余弦相似度."""

    def __init__(
        self,
        embedder: Embedder | None,
        store: Any | None = None,
        *,
        model: str | None = None,
        resolver=None,
    ) -> None:
        self._embedder = embedder
        self._resolver = resolver
        self._store = store
        self._model = model or getattr(embedder, "model_name", "") or ""

    async def score(
        self, query: str, messages: list[HistoryMessage]
    ) -> list[float]:
        if not messages:
            return []

        embedder = self._embedder
        if self._resolver is not None:
            embedder = await self._resolver()
        if embedder is None:
            return [1.0] * len(messages)
        model = str(
            getattr(embedder, "_forge_model_id", "")
            or getattr(embedder, "model_name", "")
            or self._model
        )

        cached: dict[str, list[float]] = {}
        if self._store is not None:
            try:
                cached = await self._store.batch_get(
                    [m.id for m in messages], model=model
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("消息向量缓存读取失败, 保留全部: %s", exc)
                return [1.0] * len(messages)

        raw = await asyncio.to_thread(embedder.embed_query, query)
        query_vec = _as_vector(raw)

        scores: list[float] = []
        for message in messages:
            vec = cached.get(message.id)
            if not vec or len(vec) != len(query_vec):
                scores.append(1.0)
                continue
            scores.append(_cosine_similarity(query_vec, vec))
        return scores


class HybridFilter(HistoryFilter):
    """近期锚点 + 语义过滤."""

    def __init__(
        self,
        scorer: Any | None = None,
        min_score: float = 0.6,
        anchor_turns: int = 3,
    ) -> None:
        self._scorer = scorer
        self._min_score = min_score
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

        filtered_candidates = await self._filter_candidates(candidate_msgs, query)

        # 合并 + 按原顺序排序 (turn_index 升序)
        out = filtered_candidates + anchor_msgs
        out.sort(key=lambda m: m.turn_index)
        return out

    async def _filter_candidates(
        self,
        messages: list[HistoryMessage],
        query: str,
    ) -> list[HistoryMessage]:
        if not messages or self._scorer is None:
            return messages

        # 每轮取「用户提问」那条作代表, 整轮只打一次分 (与冷路径只存提问向量对齐)。
        representatives: dict[int, HistoryMessage] = {}
        for m in messages:
            if m.message.role == "user" and m.turn_index not in representatives:
                representatives[m.turn_index] = m
        reps = list(representatives.values())
        if not reps:
            return messages  # 无提问代表 (异常), 保守保留全部

        try:
            scores = await self._scorer.score(query, reps)
        except Exception as exc:  # noqa: BLE001
            logger.warning("HybridFilter 语义打分失败, 保留全部早期消息: %s", exc)
            return messages

        turn_scores: dict[int, float] = {
            rep.turn_index: score
            for rep, score in zip(reps, scores, strict=False)
        }

        kept_turns: set[int] = set()
        for m in messages:
            # 无代表的 turn (没有 user 提问) 默认保留 (score=1.0)
            turn_score = turn_scores.get(m.turn_index, 1.0)
            m.relevance_score = turn_score
            if turn_score >= self._min_score:
                kept_turns.add(m.turn_index)
        return [m for m in messages if m.turn_index in kept_turns]


def _as_vector(raw: Any) -> list[float]:
    """把 embed_query 返回值规整成单条向量 list[float]."""
    if raw and isinstance(raw[0], list | tuple):
        return list(raw[0])
    return list(raw or [])


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """纯 Python 余弦相似度 (委托共享实现)."""
    from forge.utils.vector import cosine_similarity

    return cosine_similarity(a, b)
