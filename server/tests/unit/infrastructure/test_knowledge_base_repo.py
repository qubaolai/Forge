from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture
async def factory(tmp_path):
    import forge.infrastructure.database.orm  # noqa: F401
    from forge.infrastructure.database.orm.base import Base

    db_path = tmp_path / "kb.sqlite"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_update_stats_is_atomic_when_sessions_hold_stale_kb(factory):
    from forge.infrastructure.database.orm.knowledge_base_orm import KnowledgeBaseOrm
    from forge.infrastructure.database.repositories.knowledge_base_repo import (
        KnowledgeBaseRepository,
    )

    async with factory() as db:
        db.add(
            KnowledgeBaseOrm(
                id=1001,
                name="kb",
                visibility="private",
                owner_id=1,
                document_count=0,
                chunk_count=0,
                size_bytes=0,
            )
        )
        await db.commit()

    async with factory() as db_a, factory() as db_b:
        # 两个 session 都先加载旧值。旧实现会从各自 identity map 中拿到 0,
        # 然后分别写回 1, 最终丢失一次增量。
        assert (await db_a.get(KnowledgeBaseOrm, 1001)).document_count == 0
        assert (await db_b.get(KnowledgeBaseOrm, 1001)).document_count == 0

        await KnowledgeBaseRepository(db_a).update_stats(
            "1001",
            document_count_delta=1,
            size_bytes_delta=10,
        )
        await db_a.commit()

        await KnowledgeBaseRepository(db_b).update_stats(
            "1001",
            document_count_delta=1,
            size_bytes_delta=20,
        )
        await db_b.commit()

    async with factory() as db:
        kb = await db.get(KnowledgeBaseOrm, 1001)
        assert kb.document_count == 2
        assert kb.size_bytes == 30


@pytest.mark.asyncio
async def test_update_stats_clamps_negative_values(factory):
    from forge.infrastructure.database.orm.knowledge_base_orm import KnowledgeBaseOrm
    from forge.infrastructure.database.repositories.knowledge_base_repo import (
        KnowledgeBaseRepository,
    )

    async with factory() as db:
        db.add(
            KnowledgeBaseOrm(
                id=1001,
                name="kb",
                visibility="private",
                owner_id=1,
                document_count=1,
                chunk_count=2,
                size_bytes=10,
            )
        )
        await db.flush()

        await KnowledgeBaseRepository(db).update_stats(
            "1001",
            document_count_delta=-5,
            chunk_count_delta=-5,
            size_bytes_delta=-50,
        )
        await db.commit()

    async with factory() as db:
        kb = await db.get(KnowledgeBaseOrm, 1001)
        assert kb.document_count == 0
        assert kb.chunk_count == 0
        assert kb.size_bytes == 0
