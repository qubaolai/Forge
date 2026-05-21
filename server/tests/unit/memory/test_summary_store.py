"""SummaryStore 单测.

DB 集成测试 (真 MySQL) 留给后续 PR 处理. 这里用 FakeSessionFactory 覆盖:
    1. get(): 行不存在 -> None
    2. get(): 行存在 -> ORM 字段被正确映射成 Summary
    3. get(): SQLAlchemyError -> MemoryStoreError
    4. upsert(): SQLAlchemyError -> MemoryStoreError
    5. delete(): SQLAlchemyError -> MemoryStoreError
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from forge.memory.base import MemoryStoreError
from forge.memory.summary.store import SummaryStore


def _make_factory(*, scalar_return=None, raise_exc: Exception | None = None):
    """造一个 async_sessionmaker 替身.

    factory() -> async ctx manager -> session
    session.execute -> Result
    Result.scalar_one_or_none() -> scalar_return
    Result.scalar_one() -> scalar_return
    """
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.close = AsyncMock()

    if raise_exc is not None:
        session.execute.side_effect = raise_exc
    else:
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=scalar_return)
        result.scalar_one = MagicMock(return_value=scalar_return)
        session.execute.return_value = result

    ctx = AsyncMock()
    ctx.__aenter__.return_value = session
    ctx.__aexit__.return_value = None

    factory = MagicMock(return_value=ctx)
    return factory, session


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_get_returns_none_when_row_missing() -> None:
    factory, _ = _make_factory(scalar_return=None)
    store = SummaryStore(factory)
    assert await store.get("sess_missing") is None


@pytest.mark.asyncio
async def test_get_maps_orm_row_to_summary() -> None:
    now = datetime(2026, 5, 17, 12, 0, 0)
    row = SimpleNamespace(
        session_id="sess_abc",
        content="用户在做 RAG 项目",
        covered_until_message_id="msg_99",
        token_count=128,
        version=3,
        updated_at=now,
    )
    factory, _ = _make_factory(scalar_return=row)
    store = SummaryStore(factory)

    summary = await store.get("sess_abc")
    assert summary is not None
    assert summary.session_id == "sess_abc"
    assert summary.content == "用户在做 RAG 项目"
    assert summary.covered_until_message_id == "msg_99"
    assert summary.token_count == 128
    assert summary.version == 3
    assert summary.updated_at == now


@pytest.mark.asyncio
async def test_get_wraps_db_error() -> None:
    factory, _ = _make_factory(raise_exc=SQLAlchemyError("db down"))
    store = SummaryStore(factory)
    with pytest.raises(MemoryStoreError, match="SummaryStore.get failed"):
        await store.get("sess_abc")


# ---------------------------------------------------------------------------
# upsert / delete: 错误包装
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_upsert_wraps_db_error() -> None:
    factory, _ = _make_factory(raise_exc=SQLAlchemyError("write fail"))
    store = SummaryStore(factory)
    with pytest.raises(MemoryStoreError, match="SummaryStore.upsert failed"):
        await store.upsert(
            session_id="sess_abc",
            content="x",
            covered_until_message_id=None,
            token_count=1,
        )


@pytest.mark.asyncio
async def test_delete_wraps_db_error() -> None:
    factory, _ = _make_factory(raise_exc=SQLAlchemyError("delete fail"))
    store = SummaryStore(factory)
    with pytest.raises(MemoryStoreError, match="SummaryStore.delete failed"):
        await store.delete("sess_abc")
