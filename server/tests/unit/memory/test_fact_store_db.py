"""FactStore DB 集成测试 (SQLite): 写入/去重/召回/隔离/降级/删除/分页.

select-then-write 跨方言写法使本测试可以跑真 SQLite (照抄
tests/unit/context_mgmt/test_digest_db.py 的 StaticPool fixture)。
embedder 用确定性 fake (预置词典向量), 相似度可控。
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from forge.memory.base import FactRecallRequest
from forge.memory.facts.store import FactStore
from forge.memory.policies.conflict import ThresholdDedupResolver
from forge.memory.policies.forgetting import NoForgetting
from forge.memory.scope import MemoryScope


@pytest.fixture
async def factory():
    import forge.infrastructure.database.orm  # noqa: F401 触发 ORM 注册
    from forge.infrastructure.database.orm.base import Base

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


class FakeEmbedder:
    """确定性 embedder: 预置词典, 未知文本回退正交向量."""

    model_name = "fake-emb"
    dimension = 4

    _VOCAB = {
        "用户偏好 Python": [1.0, 0.0, 0.0, 0.0],
        "用户喜欢用 Python": [0.98, 0.2, 0.0, 0.0],  # 与上一条 cos≈0.98 (>0.92 判重)
        "用户讨厌 Java": [0.0, 1.0, 0.0, 0.0],
        "用户是后端工程师": [0.0, 0.0, 1.0, 0.0],
    }

    def _vec(self, text: str) -> list[float]:
        return self._VOCAB.get(text, [0.0, 0.0, 0.0, 1.0])

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


def _store(
    factory,
    *,
    embedder: FakeEmbedder | None = None,
    min_score: float = 0.5,
) -> FactStore:
    async def _resolve():
        return embedder

    return FactStore(
        factory,
        conflict_resolver=ThresholdDedupResolver(0.92),
        forgetting_policy=NoForgetting(),
        embedder_resolver=_resolve if embedder is not None else None,
        min_score=min_score,
        top_k_cap=20,
    )


USER_A = MemoryScope.for_user("1001")
USER_B = MemoryScope.for_user("1002")


# ---------------------------------------------------------------------------
# 写入与去重
# ---------------------------------------------------------------------------
async def test_write_inserts_fact(factory):
    store = _store(factory, embedder=FakeEmbedder())
    fact = await store.write(USER_A, content="用户偏好 Python")
    assert fact is not None and fact.id
    assert fact.user_id == "1001"

    facts, total = await store.list_by_user(USER_A)
    assert total == 1
    assert facts[0].content == "用户偏好 Python"


async def test_similar_write_skipped_by_dedup(factory):
    store = _store(factory, embedder=FakeEmbedder())
    assert await store.write(USER_A, content="用户偏好 Python") is not None
    # 高相似 (cos≈0.98 >= 0.92) -> Skip
    assert await store.write(USER_A, content="用户喜欢用 Python") is None

    _, total = await store.list_by_user(USER_A)
    assert total == 1


async def test_dissimilar_write_inserted(factory):
    store = _store(factory, embedder=FakeEmbedder())
    await store.write(USER_A, content="用户偏好 Python")
    assert await store.write(USER_A, content="用户讨厌 Java") is not None

    _, total = await store.list_by_user(USER_A)
    assert total == 2


async def test_write_records_source_session(factory):
    store = _store(factory, embedder=FakeEmbedder())
    fact = await store.write(
        USER_A, content="用户偏好 Python", source_session_id="9001"
    )
    assert fact is not None and fact.source_session_id == "9001"


# ---------------------------------------------------------------------------
# 召回: 语义 / 隔离 / min_score / 降级
# ---------------------------------------------------------------------------
async def test_recall_semantic_top_k(factory):
    store = _store(factory, embedder=FakeEmbedder())
    await store.write(USER_A, content="用户偏好 Python")
    await store.write(USER_A, content="用户是后端工程师")

    facts = await store.recall(
        FactRecallRequest(user_id="1001", query="用户偏好 Python", top_k=5)
    )
    assert [f.content for f in facts] == ["用户偏好 Python"]
    assert facts[0].score > 0.9


async def test_recall_isolated_by_user(factory):
    store = _store(factory, embedder=FakeEmbedder())
    await store.write(USER_A, content="用户偏好 Python")

    facts = await store.recall(
        FactRecallRequest(user_id="1002", query="用户偏好 Python", top_k=5)
    )
    assert facts == []


async def test_recall_min_score_filters_unrelated(factory):
    store = _store(factory, embedder=FakeEmbedder(), min_score=0.5)
    await store.write(USER_A, content="用户偏好 Python")

    # 正交 query: 有候选行但全低分 -> 返回空, 不回退 recency
    facts = await store.recall(
        FactRecallRequest(user_id="1001", query="用户讨厌 Java", top_k=5)
    )
    assert facts == []


async def test_recall_falls_back_to_recency_without_embedder(factory):
    # 写入时有 embedder
    store = _store(factory, embedder=FakeEmbedder())
    await store.write(USER_A, content="用户偏好 Python")
    await store.write(USER_A, content="用户讨厌 Java")

    # 读取时 embedder 不可用 -> recency 兜底, score=0
    degraded = _store(factory, embedder=None)
    facts = await degraded.recall(
        FactRecallRequest(user_id="1001", query="随便问问", top_k=5)
    )
    assert len(facts) == 2
    assert all(f.score == 0.0 for f in facts)


async def test_recall_model_mismatch_falls_back_to_recency(factory):
    store = _store(factory, embedder=FakeEmbedder())
    await store.write(USER_A, content="用户偏好 Python")

    # 换了 embedding 模型: 旧向量 model 不匹配 -> 无候选 -> recency 兜底
    new_embedder = FakeEmbedder()
    new_embedder.model_name = "new-emb-v2"
    switched = _store(factory, embedder=new_embedder)
    facts = await switched.recall(
        FactRecallRequest(user_id="1001", query="用户偏好 Python", top_k=5)
    )
    assert len(facts) == 1
    assert facts[0].score == 0.0


# ---------------------------------------------------------------------------
# 删除与分页
# ---------------------------------------------------------------------------
async def test_delete_scoped_to_owner(factory):
    store = _store(factory, embedder=FakeEmbedder())
    fact = await store.write(USER_A, content="用户偏好 Python")
    assert fact is not None

    # 他人删除 -> False, 行还在
    assert await store.delete(USER_B, fact.id) is False
    _, total = await store.list_by_user(USER_A)
    assert total == 1

    # 本人删除 -> True
    assert await store.delete(USER_A, fact.id) is True
    _, total = await store.list_by_user(USER_A)
    assert total == 0


async def test_list_by_user_pagination(factory):
    store = _store(factory, embedder=FakeEmbedder())
    for content in ("用户偏好 Python", "用户讨厌 Java", "用户是后端工程师"):
        await store.write(USER_A, content=content)

    page1, total = await store.list_by_user(USER_A, page=1, page_size=2)
    page2, _ = await store.list_by_user(USER_A, page=2, page_size=2)
    assert total == 3
    assert len(page1) == 2 and len(page2) == 1
    # 不重叠
    assert {f.id for f in page1}.isdisjoint({f.id for f in page2})
