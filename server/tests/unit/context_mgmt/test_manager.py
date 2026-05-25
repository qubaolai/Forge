"""ContextManager 集成测试: build + 压缩 + 用量缓存."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from forge.context_mgmt.builder.factory import build_context_builder
from forge.context_mgmt.compaction.controller import CompactionController
from forge.context_mgmt.compaction.strategies.null import NullCompaction
from forge.context_mgmt.compaction.trigger.threshold import ThresholdTrigger
from forge.context_mgmt.manager import ContextManager
from forge.context_mgmt.meter.token_meter import DefaultTokenMeter
from forge.context_mgmt.types import (
    CompactionResult,
    ContextMode,
    ContextRequest,
)
from forge.llm.token_counter import HeuristicCounter
from forge.memory.null import NullMemoryStore


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


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------
def _make_request(message="hi", context_window=4096) -> ContextRequest:
    return ContextRequest(
        user_id="u1",
        session_id="s1",
        current_user_message=message,
        mode=ContextMode.CHAT,
        system_prompt_override="你是助手",
        context_window=context_window,
    )


def _make_manager(rows, strategy=None, trigger=None):
    meter = DefaultTokenMeter(HeuristicCounter())
    builder = build_context_builder(
        mode=ContextMode.CHAT,
        message_store=FakeRepo(rows),
        memory_store=NullMemoryStore(),
        token_meter=meter,
    )
    controller = CompactionController(
        strategy or NullCompaction(),
        trigger or ThresholdTrigger(0.85),
    )
    return ContextManager(builder, controller)


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
    # layer 列表完整 (7 个标准层 + tool_results)
    layer_names = [l.name for l in cached.layers]
    assert layer_names == [
        "system_prompt", "workspace", "facts", "summary",
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
