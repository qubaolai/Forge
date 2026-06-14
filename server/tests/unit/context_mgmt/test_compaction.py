"""CompactionController + 各 Trigger / Strategy 的单测."""

from __future__ import annotations

import pytest

from forge.context_mgmt.compaction.controller import CompactionController
from forge.context_mgmt.compaction.trigger.composite import CompositeTrigger
from forge.context_mgmt.compaction.trigger.explicit import ExplicitTrigger
from forge.context_mgmt.compaction.trigger.threshold import ThresholdTrigger
from forge.context_mgmt.protocols import CompactionError
from forge.context_mgmt.types import (
    CompactionResult,
    ContextSnapshot,
    ContextUsage,
    WindowBudget,
)


def _make_snapshot(*, total_tokens: int, dropped: int = 0, window: int = 1000) -> ContextSnapshot:
    return ContextSnapshot(
        messages=[],
        budget=WindowBudget(
            context_window=window,
            system_budget=int(window * 0.15),
            dialogue_budget=int(window * 0.40),
            tool_result_budget=int(window * 0.15),
        ),
        usage=ContextUsage(
            context_window=window,
            total_input_tokens=total_tokens,
            max_output_tokens=window - total_tokens,
            total_ratio=total_tokens / window,
        ),
        history_messages_dropped=dropped,
    )


# ---------------------------------------------------------------------------
# 1. ThresholdTrigger
# ---------------------------------------------------------------------------
def test_threshold_trigger_below_ratio():
    trigger = ThresholdTrigger(0.85)
    snap = _make_snapshot(total_tokens=500)  # 50%
    assert trigger.should_compact(snap) is False


def test_threshold_trigger_above_ratio():
    trigger = ThresholdTrigger(0.85)
    snap = _make_snapshot(total_tokens=900)  # 90%
    assert trigger.should_compact(snap) is True


def test_threshold_trigger_history_dropped():
    """即使 ratio 低, 只要历史被裁过就触发."""
    trigger = ThresholdTrigger(0.85)
    snap = _make_snapshot(total_tokens=500, dropped=2)
    assert trigger.should_compact(snap) is True


# ---------------------------------------------------------------------------
# 2. ExplicitTrigger
# ---------------------------------------------------------------------------
def test_explicit_trigger_always_true():
    trigger = ExplicitTrigger()
    assert trigger.should_compact(_make_snapshot(total_tokens=0)) is True
    assert trigger.should_compact(_make_snapshot(total_tokens=999)) is True


# ---------------------------------------------------------------------------
# 3. CompositeTrigger (OR 逻辑)
# ---------------------------------------------------------------------------
def test_composite_trigger_any_true():
    """子 trigger 任一为 True 则触发."""
    composite = CompositeTrigger([ThresholdTrigger(0.85), ExplicitTrigger()])
    assert composite.should_compact(_make_snapshot(total_tokens=100)) is True


def test_composite_trigger_all_false():
    composite = CompositeTrigger([ThresholdTrigger(0.99), ThresholdTrigger(0.85)])
    assert composite.should_compact(_make_snapshot(total_tokens=100)) is False


# ---------------------------------------------------------------------------
# 4. CompactionController.compact_if_needed
# ---------------------------------------------------------------------------
class _SuccessStrategy:
    @property
    def name(self) -> str:
        return "fake_success"

    async def compact(self, session_id, snapshot):
        return CompactionResult(
            success=True, tokens_saved=1234, strategy_used=self.name,
        )


class _FailStrategy:
    @property
    def name(self) -> str:
        return "fake_fail"

    async def compact(self, session_id, snapshot):
        raise CompactionError("LLM down")


@pytest.mark.asyncio
async def test_compact_if_needed_skips_below_threshold():
    """不满足触发条件 -> 返回 None, 不调 strategy."""
    controller = CompactionController(_SuccessStrategy(), ThresholdTrigger(0.85))
    snap = _make_snapshot(total_tokens=100)  # 10%
    result = await controller.compact_if_needed("s1", snap)
    assert result is None


@pytest.mark.asyncio
async def test_compact_if_needed_executes_above_threshold():
    """满足触发条件 -> 调 strategy 并带回 trigger_source."""
    controller = CompactionController(_SuccessStrategy(), ThresholdTrigger(0.85))
    snap = _make_snapshot(total_tokens=950)  # 95%
    result = await controller.compact_if_needed("s1", snap)
    assert result is not None
    assert result.success is True
    assert result.trigger_source == "threshold"
    assert result.tokens_saved == 1234


@pytest.mark.asyncio
async def test_compact_if_needed_notifies_before_strategy():
    """触发后的回调必须在耗时 strategy 执行前完成."""
    events: list[str] = []

    class _OrderedStrategy(_SuccessStrategy):
        async def compact(self, session_id, snapshot):
            events.append("strategy")
            return await super().compact(session_id, snapshot)

    async def on_started(snapshot):
        events.append("started")

    controller = CompactionController(_OrderedStrategy(), ThresholdTrigger(0.85))
    await controller.compact_if_needed(
        "s1",
        _make_snapshot(total_tokens=950),
        on_compaction_started=on_started,
    )

    assert events == ["started", "strategy"]


# ---------------------------------------------------------------------------
# 5. compact_now 绕过 trigger
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_compact_now_bypasses_threshold():
    """compact_now 无需满足条件, 直接执行 + trigger_source='explicit'."""
    # 故意用一个永不触发的 ThresholdTrigger(0.99) - compact_now 应当无视
    controller = CompactionController(_SuccessStrategy(), ThresholdTrigger(0.99))
    result = await controller.compact_now("s1")
    assert result.success is True
    assert result.trigger_source == "explicit"


@pytest.mark.asyncio
async def test_compact_now_with_failing_strategy():
    """strategy 抛 CompactionError -> result.success=False, 不向上抛."""
    controller = CompactionController(_FailStrategy(), ExplicitTrigger())
    result = await controller.compact_now("s1")
    assert result.success is False
    assert result.trigger_source == "explicit"
    assert "LLM down" in result.failure_reason
