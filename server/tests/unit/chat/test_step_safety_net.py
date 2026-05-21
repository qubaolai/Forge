"""StepSafetyNet 单测.

覆盖:
    1. 离上限很远 -> 不引导 (None)
    2. 进入 warning_window -> hint severity
    3. 最后一步 -> force_stop severity
    4. 越界 (step >= max) -> force_stop 兜底
"""

from __future__ import annotations

import pytest

from forge.chat.guards import StepSafetyNet
from forge.chat.guards.base import LoopState


def _state(step: int, max_steps: int = 50) -> LoopState:
    return LoopState(step_index=step, max_steps=max_steps)


@pytest.mark.asyncio
async def test_far_from_limit_no_guidance() -> None:
    guard = StepSafetyNet()
    assert await guard.before_step(_state(step=5, max_steps=50)) is None
    assert await guard.before_step(_state(step=30, max_steps=50)) is None


@pytest.mark.asyncio
async def test_warning_window_emits_hint() -> None:
    guard = StepSafetyNet(warning_window=3)
    # 还剩 3 步 (step_index 47, max 50) -> hint
    g = await guard.before_step(_state(step=47, max_steps=50))
    assert g is not None
    assert g.severity == "hint"
    assert "3 步" in g.content


@pytest.mark.asyncio
async def test_last_step_force_stops() -> None:
    guard = StepSafetyNet()
    # 还剩 1 步 -> force_stop
    g = await guard.before_step(_state(step=49, max_steps=50))
    assert g is not None
    assert g.severity == "force_stop"
    assert "最后一步" in g.content


@pytest.mark.asyncio
async def test_over_limit_force_stops() -> None:
    """理论不会到这, 但兜底."""
    guard = StepSafetyNet()
    g = await guard.before_step(_state(step=50, max_steps=50))
    assert g is not None
    assert g.severity == "force_stop"
