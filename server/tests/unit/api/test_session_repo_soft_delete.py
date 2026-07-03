"""软删会话后不应再被 list / 详情返回 (bug: 删除后接口仍返回被删会话)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.repositories.chat_session_repo import (
    ChatSessionRepository,
)


@pytest_asyncio.fixture
async def maker():
    import forge.infrastructure.database.orm.chat_session_orm  # noqa: F401  注册表

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_soft_deleted_session_excluded_from_list_and_detail(maker):
    async with maker() as db:
        repo = ChatSessionRepository(db)
        a = await repo.create(user_id="1001", title="会话A")
        await repo.create(user_id="1001", title="会话B")
        await db.commit()

        _, total = await repo.list_by_user("1001", 1, 50)
        assert total == 2

        await repo.delete(a)
        await db.commit()

        # 列表: 已删会话不再出现
        items, total = await repo.list_by_user("1001", 1, 50)
        assert total == 1
        assert [s.title for s in items] == ["会话B"]

        # 详情/归属校验路径: get_active_by_id 视已删为不存在 (修复 bug 核心)
        assert await repo.get_active_by_id(a.id) is None
        # 底层 get_by_id 仍可取到 (供 chat turn 内部按需), 不受影响
        assert await repo.get_by_id(a.id) is not None
