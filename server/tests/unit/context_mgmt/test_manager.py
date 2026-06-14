"""ContextManager 集成测试: build + 压缩 + 用量缓存."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from forge.context_mgmt.builder.factory import build_context_builder
from forge.context_mgmt.compaction.controller import CompactionController
from forge.context_mgmt.compaction.trigger.explicit import ExplicitTrigger
from forge.context_mgmt.compaction.trigger.threshold import ThresholdTrigger
from forge.context_mgmt.manager import ContextManager
from forge.context_mgmt.meter.token_meter import DefaultTokenMeter
from forge.context_mgmt.types import (
    CompactionResult,
    ContextRequest,
)
from forge.llm.token_counter import HeuristicCounter
from forge.memory.null import NullMemoryStore


class _NoopStrategy:
    """不压缩的占位策略 (用于不触发压缩的用例)."""

    @property
    def name(self) -> str:
        return "noop"

    async def compact(self, session_id, snapshot):
        return CompactionResult(success=False, strategy_used=self.name)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
@dataclass
class FakeRow:
    id: str
    role: str
    content: str


class FakeRepo:
    def __init__(self, rows=None):
        self.rows = rows or []

    async def load_recent(self, session_id, limit):
        return self.rows


class _CountingStrategy:
    """记录被调用的次数, 用于验证重建逻辑."""

    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "counting"

    async def compact(self, session_id, snapshot):
        self.calls += 1
        return CompactionResult(
            success=True, strategy_used=self.name,
        )


class _FailingStrategy:
    @property
    def name(self) -> str:
        return "failing"

    async def compact(self, session_id, snapshot):
        from forge.context_mgmt.protocols import CompactionError

        raise CompactionError("db down")


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------
def _make_request(message="hi", context_window=4096) -> ContextRequest:
    return ContextRequest(
        user_id="u1",
        session_id="s1",
        current_user_message=message,
        system_prompt_override="你是助手",
        context_window=context_window,
    )


def _make_manager(rows, strategy=None, trigger=None):
    meter = DefaultTokenMeter(HeuristicCounter())
    builder = build_context_builder(
        message_store=FakeRepo(rows),
        memory_store=NullMemoryStore(),
        token_meter=meter,
    )
    controller = CompactionController(
        strategy or _NoopStrategy(),
        trigger or ThresholdTrigger(0.85),
    )

    async def build_once(request):
        return await builder.build(request)

    return ContextManager(build_once, controller)


@pytest.mark.asyncio
async def test_build_below_threshold_no_compaction():
    """正常构建, 未触发压缩."""
    manager = _make_manager([FakeRow("m1", "user", "Q"), FakeRow("m2", "assistant", "A")])
    snapshot = await manager.build(_make_request())

    assert snapshot.compaction_performed is False
    assert snapshot.rebuild_count == 0
    assert "active_compaction_triggered" not in snapshot.degraded


@pytest.mark.asyncio
async def test_usage_cache_populated_after_build():
    """build 后, get_current_usage 立即可查到."""
    manager = _make_manager([FakeRow("m1", "user", "Q")])
    assert manager.get_current_usage("s1") is None

    snapshot = await manager.build(_make_request())
    cached = manager.get_current_usage("s1")
    assert cached is not None
    assert cached.total_input_tokens == snapshot.usage.total_input_tokens
    # 无 workspace_context 时 workspace 层不记录, 其余标准层完整.
    layer_names = [layer.name for layer in cached.layers]
    assert layer_names == [
        "system_prompt", "facts", "summary",
        "dialogue", "tool_results", "current_input",
    ]


@pytest.mark.asyncio
async def test_invalidate_usage():
    manager = _make_manager([FakeRow("m1", "user", "Q")])
    await manager.build(_make_request())
    assert manager.get_current_usage("s1") is not None
    manager.invalidate_usage("s1")
    assert manager.get_current_usage("s1") is None


@pytest.mark.asyncio
async def test_compaction_triggered_rebuilds_context():
    """超阈值时触发压缩, snapshot.compaction_performed=True + rebuild_count+=1."""
    # 构造大历史触发 history_messages_dropped
    big = "字" * 400
    rows = [
        FakeRow(f"m{i}", "user" if i % 2 == 0 else "assistant", big)
        for i in range(6)
    ]
    strategy = _CountingStrategy()
    manager = _make_manager(rows, strategy=strategy)
    # 极小窗口确保 history 被截 + ratio 超阈值
    snapshot = await manager.build(_make_request(context_window=800))

    assert strategy.calls == 1
    assert snapshot.compaction_performed is True
    assert snapshot.rebuild_count == 1
    assert "active_compaction_triggered" in snapshot.degraded
    # 用量缓存反映重建后的值
    cached = manager.get_current_usage("s1")
    assert cached.total_input_tokens == snapshot.usage.total_input_tokens


@pytest.mark.asyncio
async def test_allow_compaction_false_skips_triggered_compaction():
    """调用方可禁止本次主动压缩, 即使上下文已超过触发条件."""
    strategy = _CountingStrategy()
    manager = _make_manager(
        [FakeRow("m1", "user", "Q")],
        strategy=strategy,
        trigger=ExplicitTrigger(),
    )

    snapshot = await manager.build(_make_request(), allow_compaction=False)

    assert strategy.calls == 0
    assert snapshot.compaction_performed is False


@pytest.mark.asyncio
async def test_compaction_callbacks_wrap_strategy_and_rebuild():
    """started 在 strategy 前, done 在成功重建后, 且 tokens_saved 已更新."""
    snapshots = [
        await _make_manager([FakeRow("m1", "user", "Q")]).build(_make_request()),
        await _make_manager([]).build(_make_request()),
    ]
    events: list[str] = []

    class _OrderedStrategy(_CountingStrategy):
        async def compact(self, session_id, snapshot):
            events.append("strategy")
            return await super().compact(session_id, snapshot)

    build_once = AsyncMock(side_effect=snapshots)
    manager = ContextManager(
        build_once,
        CompactionController(_OrderedStrategy(), ExplicitTrigger()),
    )

    async def on_started(snapshot):
        events.append("started")

    async def on_done(result, snapshot):
        events.append("done")
        assert result.tokens_saved == max(
            0,
            snapshots[0].usage.total_input_tokens
            - snapshots[1].usage.total_input_tokens,
        )
        assert snapshot.compaction_performed is True

    result = await manager.build(
        _make_request(),
        on_compaction_started=on_started,
        on_compaction_done=on_done,
    )

    assert build_once.await_count == 2
    assert events == ["started", "strategy", "done"]
    assert result is snapshots[1]


@pytest.mark.asyncio
async def test_compaction_failure_returns_original_snapshot_and_calls_done():
    """压缩失败保留原 snapshot, 标记降级, 并通知调用方完成事件."""
    original = await _make_manager([FakeRow("m1", "user", "Q")]).build(_make_request())
    build_once = AsyncMock(return_value=original)
    manager = ContextManager(
        build_once,
        CompactionController(_FailingStrategy(), ExplicitTrigger()),
    )
    done = AsyncMock()

    result = await manager.build(_make_request(), on_compaction_done=done)

    assert result is original
    assert "compaction_failed" in result.degraded
    assert build_once.await_count == 1
    done.assert_awaited_once()
    compaction_result, callback_snapshot = done.await_args.args
    assert compaction_result.success is False
    assert callback_snapshot is original


@pytest.mark.asyncio
async def test_compact_now_through_manager():
    """ContextManager.compact_now 转发到 CompactionController."""
    strategy = _CountingStrategy()
    manager = _make_manager(
        [FakeRow("m1", "user", "Q")],
        strategy=strategy,
        trigger=ThresholdTrigger(0.99),  # 永不自动触发
    )
    # 不调 build 直接 compact_now
    result = await manager.compact_now("s1")
    assert result.success is True
    assert result.trigger_source == "explicit"
    assert strategy.calls == 1
