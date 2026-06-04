"""memory.hooks 单测.

覆盖触发逻辑 (不依赖真实 DB / EventBus / Celery):
    1. enabled=False -> 不订阅
    2. every_n_turns <= 0 -> 不订阅
    3. message count 未到阈值 -> 不 submit
    4. message count 正好到阈值 (N*2) -> submit
    5. message count 是阈值的倍数 -> submit
    6. payload 缺 session_id -> 不 submit, 不抛
    7. DB 查询失败 -> 不 submit, 不抛
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.memory.hooks import (
    EVENT_TURN_COMPLETED,
    TASK_SUMMARIZE,
    install_memory_hooks,
)


class FakeBus:
    """记录 subscribe 调用; 让测试直接拿到 handler 自己 call."""

    def __init__(self) -> None:
        self.subs: dict[str, list] = {}

    def subscribe(self, event_name, handler):
        self.subs.setdefault(event_name, []).append(handler)


class FakeQueue:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def submit(self, task_name, **kwargs):
        self.calls.append((task_name, kwargs))


def _patch_repo_count(count: int):
    """Patch MessageRepository.count_by_session 返回固定值; 同时 patch
    get_session_factory 让 async with 不真去开连接."""
    fake_session = MagicMock()
    fake_session.close = AsyncMock()
    fake_session.commit = AsyncMock()
    fake_session.rollback = AsyncMock()

    ctx = AsyncMock()
    ctx.__aenter__.return_value = fake_session
    ctx.__aexit__.return_value = None
    factory = MagicMock(return_value=ctx)

    fake_repo = MagicMock()
    fake_repo.count_by_session = AsyncMock(return_value=count)

    return (
        patch(
            "forge.infrastructure.database.database.get_session_factory",
            return_value=factory,
        ),
        patch(
            "forge.infrastructure.database.repositories.chat_message_repo.ChatMessageRepository",
            return_value=fake_repo,
        ),
    )


# ---------------------------------------------------------------------------
# 安装阶段
# ---------------------------------------------------------------------------
def test_install_disabled_does_not_subscribe() -> None:
    bus = FakeBus()
    install_memory_hooks(bus, FakeQueue(), every_n_turns=10, enabled=False)
    assert bus.subs == {}


def test_install_zero_n_does_not_subscribe() -> None:
    bus = FakeBus()
    install_memory_hooks(bus, FakeQueue(), every_n_turns=0, enabled=True)
    assert bus.subs == {}


def test_install_registers_handler() -> None:
    bus = FakeBus()
    install_memory_hooks(bus, FakeQueue(), every_n_turns=10, enabled=True)
    assert EVENT_TURN_COMPLETED in bus.subs
    assert len(bus.subs[EVENT_TURN_COMPLETED]) == 1


# ---------------------------------------------------------------------------
# 触发条件: every_n_turns=5 -> 阈值 10 条消息
# ---------------------------------------------------------------------------
async def _run_handler(bus: FakeBus, payload: dict) -> None:
    handler = bus.subs[EVENT_TURN_COMPLETED][0]
    await handler(payload)


@pytest.mark.asyncio
async def test_below_threshold_no_submit() -> None:
    bus, queue = FakeBus(), FakeQueue()
    install_memory_hooks(bus, queue, every_n_turns=5)
    p1, p2 = _patch_repo_count(9)
    with p1, p2:
        await _run_handler(bus, {"session_id": "s1"})
    assert queue.calls == []


@pytest.mark.asyncio
async def test_at_threshold_submits() -> None:
    bus, queue = FakeBus(), FakeQueue()
    install_memory_hooks(bus, queue, every_n_turns=5)  # 阈值 = 10
    p1, p2 = _patch_repo_count(10)
    with p1, p2:
        await _run_handler(bus, {"session_id": "s1"})
    assert queue.calls == [(TASK_SUMMARIZE, {"session_id": "s1"})]


@pytest.mark.asyncio
async def test_at_threshold_submits_with_workspace_id() -> None:
    """payload 带 workspace_id 时, submit 应一并透传."""
    bus, queue = FakeBus(), FakeQueue()
    install_memory_hooks(bus, queue, every_n_turns=5)
    p1, p2 = _patch_repo_count(10)
    with p1, p2:
        await _run_handler(bus, {"session_id": "s1", "workspace_id": "ws_a"})
    assert queue.calls == [(TASK_SUMMARIZE, {"session_id": "s1", "workspace_id": "ws_a"})]


@pytest.mark.asyncio
async def test_multiple_of_threshold_submits() -> None:
    bus, queue = FakeBus(), FakeQueue()
    install_memory_hooks(bus, queue, every_n_turns=5)
    p1, p2 = _patch_repo_count(30)  # 3 * 10
    with p1, p2:
        await _run_handler(bus, {"session_id": "s1"})
    assert len(queue.calls) == 1


@pytest.mark.asyncio
async def test_zero_count_no_submit() -> None:
    """全新 session, 0 条消息: 不该 submit (虽然 0 % N == 0)."""
    bus, queue = FakeBus(), FakeQueue()
    install_memory_hooks(bus, queue, every_n_turns=5)
    p1, p2 = _patch_repo_count(0)
    with p1, p2:
        await _run_handler(bus, {"session_id": "s1"})
    assert queue.calls == []


# ---------------------------------------------------------------------------
# 异常路径
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_missing_session_id_is_silent() -> None:
    bus, queue = FakeBus(), FakeQueue()
    install_memory_hooks(bus, queue, every_n_turns=5)
    await _run_handler(bus, {})  # 不抛即可
    assert queue.calls == []


@pytest.mark.asyncio
async def test_db_failure_is_swallowed() -> None:
    bus, queue = FakeBus(), FakeQueue()
    install_memory_hooks(bus, queue, every_n_turns=5)

    with patch(
        "forge.infrastructure.database.database.get_session_factory",
        side_effect=RuntimeError("db down"),
    ):
        await _run_handler(bus, {"session_id": "s1"})  # 不抛
    assert queue.calls == []
