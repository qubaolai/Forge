"""HistoryFilter 实现单测."""

from __future__ import annotations

import pytest

from forge.context_mgmt.filters.hybrid import EmbeddingScorer, HybridFilter
from forge.context_mgmt.filters.null import NullFilter
from forge.context_mgmt.types import ContextMode, HistoryMessage
from forge.core.types.message import Message


def _msg(
    turn_index: int,
    content: str = "x",
    *,
    role: str = "user",
    message_id: str | None = None,
) -> HistoryMessage:
    return HistoryMessage(
        message=Message(role=role, content=content),
        id=message_id or f"m{turn_index}",
        turn_index=turn_index,
    )


# ---------------------------------------------------------------------------
# NullFilter
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_null_filter_keeps_all():
    msgs = [_msg(i) for i in range(5)]
    out = await NullFilter().filter(msgs, "any", ContextMode.CHAT)
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
    scorer = EmbeddingScorer(_FakeEmbedder(), None)
    scores = await scorer.score("q", [_msg(0), _msg(1)])
    assert scores == [1.0, 1.0]  # 无 store -> 全部保留


@pytest.mark.asyncio
async def test_embedding_scorer_drops_irrelevant_via_hybrid():
    """EmbeddingScorer 接入 HybridFilter: 早期正交轮被剔除, 锚点保留."""
    msgs = [_msg(i) for i in range(6)]
    # 早期 m0 相关(同向), m1/m2 正交; 后 3 轮是锚点 (anchor_turns=3)
    store = _FakeEmbStore({
        "m0": [1.0, 0.0, 0.0], "m1": [0.0, 1.0, 0.0], "m2": [0.0, 1.0, 0.0],
    })
    hf = HybridFilter(
        scorer=EmbeddingScorer(_FakeEmbedder(), store),
        min_score=0.5,
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
async def test_hybrid_without_scorer_keeps_all_history():
    """未启用语义召回时, HybridFilter 不改变历史."""
    msgs = [_msg(i) for i in range(10)]
    out = await HybridFilter(anchor_turns=3).filter(msgs, "q", ContextMode.CHAT)
    assert out == msgs


@pytest.mark.asyncio
async def test_hybrid_scorer_failure_keeps_all_history():
    """语义打分失败时保留全部历史, 不向上抛."""

    class _BrokenScorer:
        async def score(self, query, messages):
            raise RuntimeError("embedder down")

    msgs = [_msg(i) for i in range(10)]
    out = await HybridFilter(scorer=_BrokenScorer()).filter(
        msgs, "q", ContextMode.CHAT
    )
    assert out == msgs


@pytest.mark.asyncio
async def test_hybrid_keeps_anchor_turns_even_if_irrelevant():
    """最近 anchor_turns 轮无条件保留, 哪怕语义不相关."""

    class _AllZeroScorer:
        async def score(self, query, messages):
            return [0.0] * len(messages)  # 所有早期消息得分为 0

    msgs = [_msg(i) for i in range(10)]
    hf = HybridFilter(
        scorer=_AllZeroScorer(),
        min_score=0.5,
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
        scorer=_SelectiveScorer(),
        min_score=0.5,
        anchor_turns=3,
    )
    out = await hf.filter(msgs, "q", ContextMode.CHAT)
    # turn_index 2 (相关) + 7,8,9 (锚点)
    assert [m.turn_index for m in out] == [2, 7, 8, 9]


@pytest.mark.asyncio
async def test_hybrid_keeps_whole_turn_when_question_is_relevant():
    """turn 粒度: 仅 user 提问代表参与打分, 命中阈值时该轮提问与回答一起保留."""

    class _UserRepScorer:
        async def score(self, query, messages):
            # 应只收到 user 提问代表 (assistant 不参与打分)
            assert all(m.message.role == "user" for m in messages)
            return [1.0 for _ in messages]

    early_turn = [
        _msg(0, "Q0", role="user", message_id="u0"),
        _msg(0, "A0", role="assistant", message_id="a0"),
    ]
    anchors = [_msg(i, f"Q{i}") for i in range(1, 4)]
    hf = HybridFilter(
        scorer=_UserRepScorer(),
        min_score=0.5,
        anchor_turns=3,
    )

    out = await hf.filter(early_turn + anchors, "q", ContextMode.CHAT)

    assert [m.id for m in out] == ["u0", "a0", "m1", "m2", "m3"]


@pytest.mark.asyncio
async def test_hybrid_drops_whole_turn_when_all_messages_are_irrelevant():
    """同轮所有消息均低于阈值时, 整轮剔除."""

    class _AllZeroScorer:
        async def score(self, query, messages):
            return [0.0] * len(messages)

    early_turn = [
        _msg(0, "Q0", role="user", message_id="u0"),
        _msg(0, "A0", role="assistant", message_id="a0"),
    ]
    anchors = [_msg(i, f"Q{i}") for i in range(1, 4)]
    hf = HybridFilter(
        scorer=_AllZeroScorer(),
        min_score=0.5,
        anchor_turns=3,
    )

    out = await hf.filter(early_turn + anchors, "q", ContextMode.CHAT)

    assert [m.id for m in out] == ["m1", "m2", "m3"]
