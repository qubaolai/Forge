"""ChatFileRepository 单元测试 (SQLite): add / get / list / bind / delete_by_session.

复用 test_fact_store_db 的内存 SQLite + StaticPool fixture 范式。
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from forge.infrastructure.database.repositories.chat_file_repo import ChatFileRepository


@pytest.fixture
async def factory():
    import forge.infrastructure.database.orm  # noqa: F401 触发 ORM 注册 (含 ChatFileOrm)
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


async def test_add_and_get(factory):
    async with factory() as db:
        repo = ChatFileRepository(db)
        meta = await repo.add(
            owner_user_id="1001",
            session_id="9001",
            source="generated",
            filename="app.py",
            storage_path="1001/9001/app.py",
            size_bytes=10,
            mime_type="text/x-python",
            content_hash="abc",
            message_id="7001",
        )
        await db.commit()
        assert meta.id and meta.owner_user_id == "1001"

        got = await repo.get_by_id(meta.id)
        assert got is not None
        assert got.filename == "app.py"
        assert got.message_id == "7001"
        assert got.source == "generated"
        assert got.size_bytes == 10


async def test_get_missing_returns_none(factory):
    async with factory() as db:
        repo = ChatFileRepository(db)
        assert await repo.get_by_id("404") is None
        assert await repo.get_by_id("not-an-int") is None


async def test_list_by_session_and_message_filter(factory):
    async with factory() as db:
        repo = ChatFileRepository(db)
        await repo.add(
            owner_user_id="1", session_id="9", source="generated",
            filename="a.py", storage_path="p/a.py", message_id="100",
        )
        await repo.add(
            owner_user_id="1", session_id="9", source="upload",
            filename="b.txt", storage_path="p/b.txt", message_id="200",
        )
        await db.commit()

        all_files = await repo.list_by_session("9")
        assert len(all_files) == 2

        only_100 = await repo.list_by_session("9", message_id="100")
        assert [f.filename for f in only_100] == ["a.py"]


async def test_bind_message(factory):
    async with factory() as db:
        repo = ChatFileRepository(db)
        meta = await repo.add(
            owner_user_id="1", session_id="9", source="upload",
            filename="x.txt", storage_path="p/x.txt",
        )
        await db.commit()
        assert meta.message_id is None

        assert await repo.bind_message(meta.id, "555") is True
        await db.commit()

        got = await repo.get_by_id(meta.id)
        assert got is not None and got.message_id == "555"


async def test_delete_by_session(factory):
    async with factory() as db:
        repo = ChatFileRepository(db)
        await repo.add(
            owner_user_id="1", session_id="9", source="generated",
            filename="a.py", storage_path="p/a.py",
        )
        await repo.add(
            owner_user_id="1", session_id="9", source="generated",
            filename="b.py", storage_path="p/b.py",
        )
        await db.commit()

        n = await repo.delete_by_session("9")
        await db.commit()
        assert n == 2
        assert await repo.list_by_session("9") == []
