"""UserFileRepository 单元测试 (SQLite): add / bind / list / orphan / delete。

复用 test_chat_file_repo 的内存 SQLite + StaticPool fixture 范式。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from forge.infrastructure.database.repositories.user_file_repo import UserFileRepository


@pytest.fixture
async def factory():
    import forge.infrastructure.database.orm  # noqa: F401 触发 ORM 注册 (含 UserFileOrm)
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


async def test_add_unbound_then_bind(factory):
    async with factory() as db:
        repo = UserFileRepository(db)
        meta = await repo.add(
            owner_user_id="1001",
            filename="report.txt",
            storage_path="1001/2026-06-16/report.txt",
            size_bytes=12,
            mime_type="text/plain",
            content_hash="h",
        )
        await db.commit()
        # 上传时与会话解耦
        assert meta.session_id is None and meta.message_id is None
        assert meta.source == "upload"

        ok = await repo.bind_session_and_message(
            meta.id, "9001", "7001", owner_user_id="1001"
        )
        await db.commit()
        assert ok is True

        got = await repo.get_by_id(meta.id)
        assert got is not None
        assert got.session_id == "9001" and got.message_id == "7001"


async def test_bind_rejects_wrong_owner(factory):
    async with factory() as db:
        repo = UserFileRepository(db)
        meta = await repo.add(owner_user_id="1", filename="a.txt", storage_path="p/a.txt")
        await db.commit()
        # 越权 owner 不允许回填
        bad = await repo.bind_session_and_message(meta.id, "9", "7", owner_user_id="999")
        await db.commit()
        assert bad is False
        got = await repo.get_by_id(meta.id)
        assert got is not None and got.session_id is None


async def test_list_by_session_only_bound(factory):
    async with factory() as db:
        repo = UserFileRepository(db)
        a = await repo.add(owner_user_id="1", filename="a.txt", storage_path="p/a.txt")
        await repo.add(owner_user_id="1", filename="b.txt", storage_path="p/b.txt")
        await db.commit()
        await repo.bind_session_and_message(a.id, "9", "100", owner_user_id="1")
        await db.commit()

        bound = await repo.list_by_session("9")
        assert [f.filename for f in bound] == ["a.txt"]


async def test_list_orphans_excludes_bound(factory):
    async with factory() as db:
        repo = UserFileRepository(db)
        bound = await repo.add(owner_user_id="1", filename="bound.txt", storage_path="p/bound.txt")
        await repo.add(owner_user_id="1", filename="orphan.txt", storage_path="p/orphan.txt")
        await db.commit()
        await repo.bind_session_and_message(bound.id, "9", "100", owner_user_id="1")
        await db.commit()

        # cutoff 取未来, 让所有「未绑会话」者命中
        cutoff = datetime.now(UTC) + timedelta(hours=1)
        orphans = await repo.list_orphans(cutoff)
        names = {o.filename for o in orphans}
        assert names == {"orphan.txt"}
        assert all(o.session_id is None for o in orphans)


async def test_list_orphans_respects_cutoff(factory):
    async with factory() as db:
        repo = UserFileRepository(db)
        await repo.add(owner_user_id="1", filename="fresh.txt", storage_path="p/fresh.txt")
        await db.commit()
        # cutoff 取过去, 刚建的文件不应命中
        cutoff = datetime.now(UTC) - timedelta(hours=1)
        assert await repo.list_orphans(cutoff) == []


async def test_delete_by_session_returns_paths(factory):
    async with factory() as db:
        repo = UserFileRepository(db)
        a = await repo.add(owner_user_id="1", filename="a.txt", storage_path="p/a.txt")
        b = await repo.add(owner_user_id="1", filename="b.txt", storage_path="p/b.txt")
        await db.commit()
        await repo.bind_session_and_message(a.id, "9", "1", owner_user_id="1")
        await repo.bind_session_and_message(b.id, "9", "2", owner_user_id="1")
        await db.commit()

        paths = await repo.delete_by_session("9")
        await db.commit()
        assert set(paths) == {"p/a.txt", "p/b.txt"}
        assert await repo.list_by_session("9") == []


async def test_delete_by_ids(factory):
    async with factory() as db:
        repo = UserFileRepository(db)
        a = await repo.add(owner_user_id="1", filename="a.txt", storage_path="p/a.txt")
        await db.commit()
        n = await repo.delete_by_ids([a.id, "not-an-int"])
        await db.commit()
        assert n == 1
        assert await repo.get_by_id(a.id) is None
