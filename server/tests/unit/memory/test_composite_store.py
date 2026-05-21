"""CompositeMemoryStore 单测.

只测 "委托给 SummaryStore" 和 "facts 阶段性返回 []" 这两件事.
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
    store.get.assert_awaited_once_with("s1", workspace_id=None)


@pytest.mark.asyncio
async def test_get_summary_passes_workspace_scope() -> None:
    store = AsyncMock()
    store.get.return_value = None
    composite = CompositeMemoryStore(summary_store=store)
    await composite.get_summary("s1", workspace_id="ws_a")
    store.get.assert_awaited_once_with("s1", workspace_id="ws_a")


@pytest.mark.asyncio
async def test_recall_facts_returns_empty_in_stage_2() -> None:
    store = AsyncMock()
    composite = CompositeMemoryStore(summary_store=store)
    facts = await composite.recall_facts(FactRecallRequest(user_id="u1", query="x", top_k=5))
    assert facts == []
