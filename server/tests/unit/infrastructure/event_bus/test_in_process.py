"""InProcessEventBus 单测.

覆盖:
    1. publish 无 subscriber -> 不抛
    2. subscribe 后 publish -> handler 被调到, 收到正确 payload
    3. 多 handler -> 都收到
    4. handler 抛异常 -> 被 EventBus 吞掉, 其他 handler 不受影响
    5. publish 立即返回 (fire-and-forget), 不等 handler 完成
"""

from __future__ import annotations

import asyncio

import pytest

from forge.infrastructure.event_bus.in_process import InProcessEventBus


@pytest.mark.asyncio
async def test_publish_with_no_subscriber_is_noop() -> None:
    bus = InProcessEventBus()
    await bus.publish("turn.completed", {"session_id": "x"})  # 不抛即可


@pytest.mark.asyncio
async def test_subscriber_receives_payload() -> None:
    bus = InProcessEventBus()
    received: list[dict] = []

    async def handler(payload: dict) -> None:
        received.append(payload)

    bus.subscribe("turn.completed", handler)
    await bus.publish("turn.completed", {"session_id": "s1"})
    # publish fire-and-forget, 等一个事件循环回合
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert received == [{"session_id": "s1"}]


@pytest.mark.asyncio
async def test_multiple_subscribers_all_called() -> None:
    bus = InProcessEventBus()
    calls: list[str] = []

    async def h1(payload: dict) -> None:
        calls.append(f"h1:{payload['x']}")

    async def h2(payload: dict) -> None:
        calls.append(f"h2:{payload['x']}")

    bus.subscribe("evt", h1)
    bus.subscribe("evt", h2)
    await bus.publish("evt", {"x": 1})
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert sorted(calls) == ["h1:1", "h2:1"]


@pytest.mark.asyncio
async def test_subscriber_exception_isolated(caplog: pytest.LogCaptureFixture) -> None:
    bus = InProcessEventBus()
    survived: list[str] = []

    async def crashy(payload: dict) -> None:
        raise RuntimeError("boom")

    async def healthy(payload: dict) -> None:
        survived.append("ok")

    bus.subscribe("evt", crashy)
    bus.subscribe("evt", healthy)
    with caplog.at_level("ERROR"):
        await bus.publish("evt", {})
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert survived == ["ok"]
    assert any("事件处理异常" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_publish_does_not_wait_for_slow_handler() -> None:
    bus = InProcessEventBus()
    started = asyncio.Event()
    finish = asyncio.Event()

    async def slow(payload: dict) -> None:
        started.set()
        await finish.wait()  # 永远不进, 直到我们 set

    bus.subscribe("evt", slow)
    # publish 应立即返回 (fire-and-forget). 完了 handler 还没跑完.
    await bus.publish("evt", {})
    # handler 才被调度上, 但还卡在 finish.wait().
    await asyncio.sleep(0)
    assert started.is_set()
    assert not finish.is_set()
    finish.set()  # 收尾, 避免悬挂 task warning
    await asyncio.sleep(0)
