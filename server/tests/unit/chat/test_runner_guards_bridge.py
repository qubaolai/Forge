"""GuardLifecycleAdapter 单测.

不跑真 ReActAgent (会调 LLM); 直接验 LoopGuard list -> AgentLifecycle 的
适配逻辑:
    1. 所有 guards 返回 None -> adapter.before_step 返回 None
    2. guard 返回 hint -> 注入文本, force_text_only=False
    3. guard 返回 force_stop -> 注入文本 + force_text_only=True
    4. 多个 guards 全部参与, 任一 force_stop -> force_text_only=True
    5. guard 抛异常 -> 跳过, 不影响其他 guards
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from forge.agents.lifecycle import StepContext
from forge.chat.guards.base import Guidance
from forge.chat.guards.lifecycle_adapter import GuardLifecycleAdapter
from forge.chat.guards.wall_clock import WallClockGuard
from forge.chat.runner import ReActRunner


class _Pass:
    async def before_step(self, state):
        return None


class _Hint:
    def __init__(self, text="hint-x"):
        self.text = text

    async def before_step(self, state):
        return Guidance(content=self.text, severity="hint")


class _Force:
    async def before_step(self, state):
        return Guidance(content="必须停", severity="force_stop")


class _Crash:
    async def before_step(self, state):
        raise RuntimeError("boom")


def _ctx(step: int = 0) -> StepContext:
    return StepContext(step_index=step, max_steps=50, messages_count=2, last_step_tool_calls=())


@pytest.mark.asyncio
async def test_all_pass_returns_none() -> None:
    """全部 pass -> 返回 None (无 decision)."""
    adapter = GuardLifecycleAdapter(cast(Any, [_Pass(), _Pass()]))
    decision = await adapter.before_step(_ctx())
    assert decision is None


@pytest.mark.asyncio
async def test_hint_injects_but_does_not_force() -> None:
    adapter = GuardLifecycleAdapter(cast(Any, [_Hint("提示 A")]))
    decision = await adapter.before_step(_ctx())
    assert decision is not None
    assert decision.inject_system_messages == ["提示 A"]
    assert decision.force_text_only is False


@pytest.mark.asyncio
async def test_force_stop_sets_text_only() -> None:
    adapter = GuardLifecycleAdapter(cast(Any, [_Force()]))
    decision = await adapter.before_step(_ctx())
    assert decision is not None
    assert decision.force_text_only is True
    assert decision.inject_system_messages == ["必须停"]


@pytest.mark.asyncio
async def test_any_force_stop_wins() -> None:
    """即使有 hint, 只要任意 force_stop, 整步 force_text_only=True."""
    adapter = GuardLifecycleAdapter(cast(Any, [_Hint("提示 A"), _Force(), _Hint("提示 B")]))
    decision = await adapter.before_step(_ctx())
    assert decision is not None
    assert decision.force_text_only is True
    assert "提示 A" in decision.inject_system_messages
    assert "必须停" in decision.inject_system_messages
    assert "提示 B" in decision.inject_system_messages


@pytest.mark.asyncio
async def test_crash_isolated() -> None:
    """一个 guard 崩了, 其他照常工作."""
    adapter = GuardLifecycleAdapter(cast(Any, [_Crash(), _Hint("仍然给提示")]))
    decision = await adapter.before_step(_ctx())
    assert decision is not None
    assert decision.inject_system_messages == ["仍然给提示"]
    assert decision.force_text_only is False


def test_default_guards_use_builtin_wall_clock_limits() -> None:
    """Runner._build_guard_factories 用内置 wall_clock 默认时限构造守护."""
    from forge.chat.runner import (
        _WALL_CLOCK_HARD_LIMIT_SEC,
        _WALL_CLOCK_SOFT_LIMIT_SEC,
        _WALL_CLOCK_WARN_LIMIT_SEC,
    )

    runner = ReActRunner(llm_chain=object(), system_prompt="")
    guards = [factory(50) for factory in runner._build_guard_factories()]
    wall = [g for g in guards if isinstance(g, WallClockGuard)][0]
    assert wall._soft == _WALL_CLOCK_SOFT_LIMIT_SEC
    assert wall._warn == _WALL_CLOCK_WARN_LIMIT_SEC
    assert wall._hard == _WALL_CLOCK_HARD_LIMIT_SEC
