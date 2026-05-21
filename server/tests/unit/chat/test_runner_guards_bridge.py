"""ReActRunner -> LoopGuard 桥接单测.

不跑真 ReActAgent (会调 LLM); 直接验 _make_before_step_bridge 的逻辑:
    1. 单个 guard 返回 None -> StepDecision 是空 (无注入, 无 force)
    2. guard 返回 hint -> 注入文本, force_stop=False
    3. guard 返回 force_stop -> 注入文本 + force_text_only=True
    4. 多个 guards 全部参与, 任一 force_stop -> force_text_only=True
    5. guard 抛异常 -> 跳过, 不影响其他 guards
"""

from __future__ import annotations

import pytest

from forge.agents.react.agent import StepContext
from forge.chat.guards.base import Guidance
from forge.chat.guards.wall_clock import WallClockGuard
from forge.chat.runner import ReActRunner
from forge.workspace.runtime import WorkspaceRuntimeSettings


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


def _runner_with(guards):
    """构造 Runner, 注入固定 guard 列表 (跳过工厂)."""
    return ReActRunner(
        llm_chain=object(),
        system_prompt="",
        guard_factories=[lambda max_steps, g=g: g for g in guards],
    )


def _ctx(step: int = 0) -> StepContext:
    return StepContext(step_index=step, max_steps=50, messages_count=2, last_step_tool_calls=())


@pytest.mark.asyncio
async def test_all_pass_returns_empty_decision() -> None:
    runner = _runner_with([_Pass(), _Pass()])
    # 模拟 run 流程: guards 在 run() 内被实例化, 这里手动跑 bridge
    guards = [factory(50) for factory in runner._guard_factories]
    bridge = runner._make_before_step_bridge(guards, run_started_at=0.0)

    decision = await bridge(_ctx())
    assert decision.inject_system_messages == []
    assert decision.force_text_only is False


@pytest.mark.asyncio
async def test_hint_injects_but_does_not_force() -> None:
    runner = _runner_with([_Hint("提示 A")])
    guards = [factory(50) for factory in runner._guard_factories]
    bridge = runner._make_before_step_bridge(guards, run_started_at=0.0)

    decision = await bridge(_ctx())
    assert decision.inject_system_messages == ["提示 A"]
    assert decision.force_text_only is False


@pytest.mark.asyncio
async def test_force_stop_sets_text_only() -> None:
    runner = _runner_with([_Force()])
    guards = [factory(50) for factory in runner._guard_factories]
    bridge = runner._make_before_step_bridge(guards, run_started_at=0.0)

    decision = await bridge(_ctx())
    assert decision.force_text_only is True
    assert decision.inject_system_messages == ["必须停"]


@pytest.mark.asyncio
async def test_any_force_stop_wins() -> None:
    """即使有 hint, 只要任意 force_stop, 整步 force_text_only=True."""
    runner = _runner_with([_Hint("提示 A"), _Force(), _Hint("提示 B")])
    guards = [factory(50) for factory in runner._guard_factories]
    bridge = runner._make_before_step_bridge(guards, run_started_at=0.0)

    decision = await bridge(_ctx())
    assert decision.force_text_only is True
    assert "提示 A" in decision.inject_system_messages
    assert "必须停" in decision.inject_system_messages
    assert "提示 B" in decision.inject_system_messages


@pytest.mark.asyncio
async def test_crash_isolated() -> None:
    """一个 guard 崩了, 其他照常工作."""
    runner = _runner_with([_Crash(), _Hint("仍然给提示")])
    guards = [factory(50) for factory in runner._guard_factories]
    bridge = runner._make_before_step_bridge(guards, run_started_at=0.0)

    decision = await bridge(_ctx())
    # crash guard 被吞, hint 仍然生效
    assert decision.inject_system_messages == ["仍然给提示"]
    assert decision.force_text_only is False


def test_default_guards_read_workspace_wall_clock(monkeypatch) -> None:
    monkeypatch.setattr(
        "forge.chat.runner.resolve_runtime_settings",
        lambda: WorkspaceRuntimeSettings(
            chat_timeout_seconds=300,
            wall_clock_soft_limit_sec=11,
            wall_clock_warn_limit_sec=22,
            wall_clock_hard_limit_sec=33,
        ),
    )
    runner = ReActRunner(llm_chain=object(), system_prompt="")
    guards = [factory(50) for factory in runner._build_guard_factories()]
    wall = [g for g in guards if isinstance(g, WallClockGuard)][0]
    assert wall._soft == 11
    assert wall._warn == 22
    assert wall._hard == 33
