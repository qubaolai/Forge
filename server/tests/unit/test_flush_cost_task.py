"""run_flush_cost_task 行为测试."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.observability.cost.tasks.flush_cost import run_flush_cost_task


@pytest.mark.asyncio
async def test_run_flush_cost_task_returns_zero_when_factory_uninitialized() -> None:
    with patch(
        "forge.infrastructure.database.database.get_session_factory",
        side_effect=RuntimeError("engine not initialized"),
    ):
        written = await run_flush_cost_task()
    assert written == 0


@pytest.mark.asyncio
async def test_run_flush_cost_task_returns_zero_when_factory_is_none() -> None:
    with patch(
        "forge.infrastructure.database.database.get_session_factory",
        return_value=None,
    ):
        written = await run_flush_cost_task()
    assert written == 0


@pytest.mark.asyncio
async def test_run_flush_cost_task_flushes_tracker() -> None:
    fake_factory = object()
    fake_tracker = MagicMock()
    fake_tracker.flush_to_db = AsyncMock(return_value=3)

    with (
        patch(
            "forge.infrastructure.database.database.get_session_factory",
            return_value=fake_factory,
        ),
        patch(
            "forge.llm.cost_tracker.get_cost_tracker",
            return_value=fake_tracker,
        ),
    ):
        written = await run_flush_cost_task()

    assert written == 3
    fake_tracker.flush_to_db.assert_awaited_once_with(fake_factory)
