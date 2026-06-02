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
