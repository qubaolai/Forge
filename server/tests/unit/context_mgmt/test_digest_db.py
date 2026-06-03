"""DB 集成测试 (SQLite): DbMessageContentStore 鉴权 + DigestStore.batch_get_meta。

用 StaticPool 让内存库在多次 session 间共享 (DbMessageContentStore 会另开 session)。
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


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


async def _seed_message(factory, *, user_id: str, content: str) -> str:
    from forge.infrastructure.database.repositories.chat_message_repo import (
        ChatMessageRepository,
    )
    from forge.infrastructure.database.repositories.chat_session_repo import (
        ChatSessionRepository,
    )
    async with factory() as db:
        sess = await ChatSessionRepository(db).create(user_id=user_id, title="t")
        msg = await ChatMessageRepository(db).add(
            session_id=sess.id, role="assistant", content=content
        )
        await db.commit()
    return msg.id


# ---------------------------------------------------------------------------
# 安全: read_message 经 owner_user_id 限定在本用户会话 (防越权)
# ---------------------------------------------------------------------------
async def test_db_content_store_scopes_to_owner(factory):
    from forge.infrastructure.storage.content_store import DbMessageContentStore

    mid = await _seed_message(factory, user_id="1001", content="l1\nl2\nsecret")
    store = DbMessageContentStore(factory)

    # 拥有者可读
    sl = await store.get(f"msg:{mid}", None, owner_user_id="1001")
    assert sl is not None and "secret" in sl.text

    # 他人不可读 -> None (不泄露存在性)
    assert await store.get(f"msg:{mid}", None, owner_user_id="1002") is None
    assert await store.exists(f"msg:{mid}", owner_user_id="1002") is False

    # 不传 owner (内部可信调用) 仍可读, 且 line_range 切片正确
    sl2 = await store.get(f"msg:{mid}", (1, 2), owner_user_id=None)
    assert sl2.text == "l1\nl2" and sl2.truncated is True


# ---------------------------------------------------------------------------
# DigestStore.batch_get_meta: 批量取 (hash, status), 缺失不在结果内
# ---------------------------------------------------------------------------
async def test_digest_store_batch_get_meta(factory):
    from forge.context_mgmt.digest.store import DigestStore
    from forge.context_mgmt.digest.types import Segment

    store = DigestStore(factory)
    await store.upsert(
        message_id="123", session_id="456",
        segments=[Segment(kind="prose", start_line=1, end_line=1, digest_text="d")],
        total_tokens=100, source_hash="h1", model=None, status="done",
    )
    metas = await store.batch_get_meta(["123", "999"])
    assert metas == {"123": ("h1", "done")}


# ---------------------------------------------------------------------------
# token_count 列: add/update 写入 + 读回 (修订 B)
# ---------------------------------------------------------------------------
async def test_token_count_roundtrip(factory):
    from forge.infrastructure.database.repositories.chat_message_repo import (
        ChatMessageRepository,
    )
    from forge.infrastructure.database.repositories.chat_session_repo import (
        ChatSessionRepository,
    )

    async with factory() as db:
        sess = await ChatSessionRepository(db).create(user_id="1", title="t")
        repo = ChatMessageRepository(db)
        msg = await repo.add(
            session_id=sess.id, role="user", content="hi", token_count=42
        )
        await db.commit()

        got = await repo.get_by_id(msg.id)
        assert got.token_count == 42

        # update 也能改 token_count
        await repo.update(got, token_count=99)
        await db.commit()
        assert (await repo.get_by_id(msg.id)).token_count == 99


# ---------------------------------------------------------------------------
# 幂等增量列迁移: 给缺 token_count 的旧表补列 (修订 B)
# ---------------------------------------------------------------------------
async def test_additive_column_migration_idempotent():
    from sqlalchemy import inspect

    from forge.infrastructure.database.database import _ensure_additive_columns

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    def _cols(sync_conn) -> set[str]:
        return {c["name"] for c in inspect(sync_conn).get_columns("chat_messages")}

    async with engine.begin() as conn:
        # 建一个「旧版」chat_messages 表 (无 token_count 列)
        await conn.exec_driver_sql(
            "CREATE TABLE chat_messages (id INTEGER PRIMARY KEY, content TEXT)"
        )
        assert "token_count" not in await conn.run_sync(_cols)
        # 第一次迁移: 补上列
        await conn.run_sync(_ensure_additive_columns)
        assert "token_count" in await conn.run_sync(_cols)
        # 第二次迁移: 幂等, 不报错
        await conn.run_sync(_ensure_additive_columns)
        assert "token_count" in await conn.run_sync(_cols)

    await engine.dispose()


# ---------------------------------------------------------------------------
# MessageEmbeddingStore: upsert + batch_get (按 model 匹配) + 幂等去重 (修订 D)
# ---------------------------------------------------------------------------
async def test_message_embedding_store_roundtrip(factory):
    from forge.context_mgmt.recall.embedding_store import MessageEmbeddingStore

    store = MessageEmbeddingStore(factory)
    await store.upsert(
        message_id="11", session_id="22", model="m-fast", dim=3,
        vector=[0.1, 0.2, 0.3], source_hash="h1",
    )

    # 模型匹配 -> 命中
    got = await store.batch_get(["11", "999"], model="m-fast")
    assert got == {"11": [0.1, 0.2, 0.3]}
    # 模型不匹配 -> 未命中 (不混用)
    assert await store.batch_get(["11"], model="other") == {}
    # meta 供冷路径去重
    metas = await store.batch_get_meta(["11"])
    assert metas == {"11": ("h1", "m-fast")}

    # upsert 覆盖 (regenerate / 模型切换)
    await store.upsert(
        message_id="11", session_id="22", model="m-strong", dim=2,
        vector=[0.5, 0.6], source_hash="h2",
    )
    assert await store.batch_get(["11"], model="m-strong") == {"11": [0.5, 0.6]}
