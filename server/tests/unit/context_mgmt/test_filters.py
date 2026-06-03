"""HistoryFilter 实现单测."""

from __future__ import annotations

import pytest

from forge.context_mgmt.filters.hybrid import HybridFilter
from forge.context_mgmt.filters.null import NullFilter
from forge.context_mgmt.filters.recent import RecentFilter
from forge.context_mgmt.filters.semantic import (
    NullScorer,
    SemanticFilter,
)
from forge.context_mgmt.filters.step_scoped import StepScopedFilter
from forge.context_mgmt.types import ContextMode, HistoryMessage
from forge.core.types.message import Message


def _msg(turn_index: int, content: str = "x") -> HistoryMessage:
    return HistoryMessage(
        message=Message(role="user", content=content),
        id=f"m{turn_index}",
        turn_index=turn_index,
    )


# ---------------------------------------------------------------------------
# RecentFilter / NullFilter
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_recent_filter_keeps_all():
    msgs = [_msg(i) for i in range(5)]
    out = await RecentFilter().filter(msgs, "any", ContextMode.CHAT)
    assert out == msgs


@pytest.mark.asyncio
async def test_null_filter_returns_empty():
    msgs = [_msg(i) for i in range(5)]
    out = await NullFilter().filter(msgs, "any", ContextMode.TASK)
    assert out == []


# ---------------------------------------------------------------------------
# SemanticFilter
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_semantic_filter_with_null_scorer_keeps_all():
    """NullScorer 给所有消息 1.0 分, 全部通过 min_score=0.6 阈值."""
    msgs = [_msg(i) for i in range(3)]
    sf = SemanticFilter(scorer=NullScorer(), min_score=0.6)
    out = await sf.filter(msgs, "query", ContextMode.CHAT)
    assert len(out) == 3


@pytest.mark.asyncio
async def test_semantic_filter_drops_low_score():
    """得分低于阈值的被剔除."""

    class _BinaryScorer:
        async def score(self, query, messages):
            # 偶数 turn_index 给 1.0, 奇数给 0.1
            return [1.0 if m.turn_index % 2 == 0 else 0.1 for m in messages]

    msgs = [_msg(i) for i in range(4)]
    sf = SemanticFilter(scorer=_BinaryScorer(), min_score=0.5)
    out = await sf.filter(msgs, "q", ContextMode.CHAT)
    assert [m.turn_index for m in out] == [0, 2]


@pytest.mark.asyncio
async def test_semantic_filter_scorer_failure_fallback():
    """Scorer 抛异常 -> 保留全部, 不向上抛."""

    class _BrokenScorer:
        async def score(self, query, messages):
            raise RuntimeError("embedder down")

    msgs = [_msg(i) for i in range(3)]
    sf = SemanticFilter(scorer=_BrokenScorer(), min_score=0.5)
    out = await sf.filter(msgs, "q", ContextMode.CHAT)
    assert out == msgs


# ---------------------------------------------------------------------------
# EmbeddingScorer (修订 D): 读缓存向量 + query 实时 embedding, 算余弦
# ---------------------------------------------------------------------------
class _FakeEmbedder:
    model_name = "fake-emb"
    dimension = 3

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]


class _FakeEmbStore:
    def __init__(self, mapping: dict[str, list[float]]) -> None:
        self._m = mapping

    async def batch_get(self, ids, *, model):
        return {k: v for k, v in self._m.items() if k in ids}


@pytest.mark.asyncio
async def test_embedding_scorer_uses_cached_vectors():
    from forge.context_mgmt.filters.semantic import EmbeddingScorer

    msgs = [_msg(0), _msg(1), _msg(2)]
    # m0 与 query 同向 (1.0); m1 正交 (0.0); m2 无缓存 (保留 1.0)
    store = _FakeEmbStore({"m0": [1.0, 0.0, 0.0], "m1": [0.0, 1.0, 0.0]})
    scorer = EmbeddingScorer(_FakeEmbedder(), store)

    scores = await scorer.score("q", msgs)
    assert scores[0] == pytest.approx(1.0)
    assert scores[1] == pytest.approx(0.0)
    assert scores[2] == 1.0  # 无缓存向量 -> 保留, 不误删


@pytest.mark.asyncio
async def test_embedding_scorer_no_store_keeps_all():
    from forge.context_mgmt.filters.semantic import EmbeddingScorer

    scorer = EmbeddingScorer(_FakeEmbedder(), None)
    scores = await scorer.score("q", [_msg(0), _msg(1)])
    assert scores == [1.0, 1.0]  # 无 store -> 全部保留


@pytest.mark.asyncio
async def test_embedding_scorer_drops_irrelevant_via_hybrid():
    """EmbeddingScorer 接入 HybridFilter: 早期正交轮被剔除, 锚点保留."""
    from forge.context_mgmt.filters.semantic import EmbeddingScorer

    msgs = [_msg(i) for i in range(6)]
    # 早期 m0 相关(同向), m1/m2 正交; 后 3 轮是锚点 (anchor_turns=3)
    store = _FakeEmbStore({
        "m0": [1.0, 0.0, 0.0], "m1": [0.0, 1.0, 0.0], "m2": [0.0, 1.0, 0.0],
    })
    hf = HybridFilter(
        semantic=SemanticFilter(EmbeddingScorer(_FakeEmbedder(), store), min_score=0.5),
        anchor_turns=3,
    )
    out = await hf.filter(msgs, "q", ContextMode.CHAT)
    # m0 相关保留 + m3,m4,m5 锚点; m1/m2 正交剔除
    assert [m.turn_index for m in out] == [0, 3, 4, 5]


# ---------------------------------------------------------------------------
# HybridFilter
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_hybrid_keeps_all_when_below_anchor():
    """轮次总数 ≤ anchor_turns -> 全部保留."""
    msgs = [_msg(i) for i in range(3)]
    hf = HybridFilter(anchor_turns=3)
    out = await hf.filter(msgs, "q", ContextMode.CHAT)
    assert len(out) == 3


@pytest.mark.asyncio
async def test_hybrid_keeps_anchor_turns_even_if_irrelevant():
    """最近 anchor_turns 轮无条件保留, 哪怕语义不相关."""

    class _AllZeroScorer:
        async def score(self, query, messages):
            return [0.0] * len(messages)  # 所有早期消息得分为 0

    msgs = [_msg(i) for i in range(10)]
    hf = HybridFilter(
        semantic=SemanticFilter(scorer=_AllZeroScorer(), min_score=0.5),
        anchor_turns=3,
    )
    out = await hf.filter(msgs, "q", ContextMode.CHAT)
    # 早期 7 轮全部剔除, 后 3 轮 (turn_index 7,8,9) 保留
    assert [m.turn_index for m in out] == [7, 8, 9]


@pytest.mark.asyncio
async def test_hybrid_keeps_relevant_early_turns():
    """早期相关性高的轮次也保留."""

    class _SelectiveScorer:
        async def score(self, query, messages):
            # 只有 turn_index=2 得高分, 其他为 0
            return [1.0 if m.turn_index == 2 else 0.0 for m in messages]

    msgs = [_msg(i) for i in range(10)]
    hf = HybridFilter(
        semantic=SemanticFilter(scorer=_SelectiveScorer(), min_score=0.5),
        anchor_turns=3,
    )
    out = await hf.filter(msgs, "q", ContextMode.CHAT)
    # turn_index 2 (相关) + 7,8,9 (锚点)
    assert [m.turn_index for m in out] == [2, 7, 8, 9]


# ---------------------------------------------------------------------------
# StepScopedFilter
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_step_scoped_filter_returns_empty():
    """阶段 4 占位实现: 返回空 (workflow 步骤无历史)."""
    msgs = [_msg(i) for i in range(5)]
    out = await StepScopedFilter().filter(msgs, "q", ContextMode.WORKFLOW)
    assert out == []
