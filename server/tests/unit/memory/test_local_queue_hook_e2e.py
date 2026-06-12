"""memory hook + LocalTaskQueue 端到端链路测试."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.infrastructure.event_bus.in_process import InProcessEventBus
from forge.infrastructure.queue.local import LocalTaskQueue
from forge.memory.hooks import EVENT_TURN_COMPLETED, install_memory_hooks


@pytest.mark.asyncio
async def test_hook_dispatches_to_local_queue_and_runs_summary_service(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "home"))
    bus = InProcessEventBus()
    queue = LocalTaskQueue()
    install_memory_hooks(bus, queue, every_n_turns=1, enabled=True)

    fake_session = MagicMock()
    fake_session.commit = AsyncMock()
    fake_session.rollback = AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__.return_value = fake_session
    ctx.__aexit__.return_value = None
    factory = MagicMock(return_value=ctx)

    fake_repo = MagicMock()
    fake_repo.count_by_session = AsyncMock(return_value=2)

    fake_service = MagicMock()
    fake_service.summarize_session = AsyncMock(return_value=None)

    with (
        patch(
            "forge.infrastructure.database.database.get_session_factory",
            return_value=factory,
        ),
        patch(
            "forge.infrastructure.database.repositories.chat_message_repo.ChatMessageRepository",
            return_value=fake_repo,
        ),
        patch(
            "forge.memory.summary.service.get_summary_service",
            return_value=fake_service,
        ),
    ):
        await bus.publish(EVENT_TURN_COMPLETED, {"session_id": "s1"})
        # publish 和 LocalTaskQueue 都是 fire-and-forget, 让出事件循环驱动两层任务.
        await asyncio.sleep(0.1)

    fake_repo.count_by_session.assert_awaited_once_with("s1")
    fake_service.summarize_session.assert_awaited_once_with("s1")
