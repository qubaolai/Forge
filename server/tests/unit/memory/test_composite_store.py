"""CompositeMemoryStore 单测.

测 "委托给 SummaryStore / FactStore" 和 "无 fact_store 时 facts 返回 []".
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from forge.memory.base import FactRecallRequest, Summary
from forge.memory.composite import CompositeMemoryStore


@pytest.mark.asyncio
async def test_get_summary_delegates_to_summary_store() -> None:
    store = AsyncMock()
    store.get.return_value = Summary(
        session_id="s1",
        content="x",
        covered_until_message_id=None,
        token_count=0,
        updated_at=datetime(2026, 1, 1),
    )
    composite = CompositeMemoryStore(summary_store=store)
    summary = await composite.get_summary("s1")
    assert summary is not None and summary.content == "x"
    store.get.assert_awaited_once_with("s1")


@pytest.mark.asyncio
async def test_recall_facts_returns_empty_without_fact_store() -> None:
    store = AsyncMock()
    composite = CompositeMemoryStore(summary_store=store)
    facts = await composite.recall_facts(FactRecallRequest(user_id="u1", query="x", top_k=5))
    assert facts == []


@pytest.mark.asyncio
async def test_recall_facts_delegates_to_fact_store() -> None:
    from forge.memory.base import Fact

    summary_store = AsyncMock()
    fact_store = AsyncMock()
    expected = [Fact(id="f1", user_id="u1", content="用户偏好 Python", source="llm_extracted")]
    fact_store.recall.return_value = expected

    composite = CompositeMemoryStore(summary_store=summary_store, fact_store=fact_store)
    request = FactRecallRequest(user_id="u1", query="偏好", top_k=5)
    facts = await composite.recall_facts(request)

    assert facts == expected
    fact_store.recall.assert_awaited_once_with(request)
