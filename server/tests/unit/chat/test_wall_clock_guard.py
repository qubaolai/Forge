"""WallClockGuard 单测."""

from __future__ import annotations

import pytest

from forge.chat.guards import WallClockGuard
from forge.chat.guards.base import LoopState


def _state(elapsed: float) -> LoopState:
    return LoopState(step_index=0, max_steps=50, elapsed_seconds=elapsed)


@pytest.mark.asyncio
async def test_no_elapsed_no_guidance() -> None:
    guard = WallClockGuard(soft_limit_sec=10, warn_limit_sec=20, hard_limit_sec=30)
    assert await guard.before_step(_state(0)) is None


@pytest.mark.asyncio
async def test_below_soft_no_guidance() -> None:
    guard = WallClockGuard(soft_limit_sec=10, warn_limit_sec=20, hard_limit_sec=30)
    assert await guard.before_step(_state(5)) is None


@pytest.mark.asyncio
async def test_hint_at_soft() -> None:
    guard = WallClockGuard(soft_limit_sec=10, warn_limit_sec=20, hard_limit_sec=30)
    g = await guard.before_step(_state(12))
    assert g is not None and g.severity == "hint"


@pytest.mark.asyncio
async def test_warning_at_warn_limit() -> None:
    guard = WallClockGuard(soft_limit_sec=10, warn_limit_sec=20, hard_limit_sec=30)
    g = await guard.before_step(_state(22))
    assert g is not None and g.severity == "warning"


@pytest.mark.asyncio
async def test_force_stop_at_hard_limit() -> None:
    guard = WallClockGuard(soft_limit_sec=10, warn_limit_sec=20, hard_limit_sec=30)
    g = await guard.before_step(_state(35))
    assert g is not None and g.severity == "force_stop"


def test_invalid_thresholds_rejected() -> None:
    with pytest.raises(ValueError):
        WallClockGuard(soft_limit_sec=20, warn_limit_sec=10, hard_limit_sec=30)
    with pytest.raises(ValueError):
        WallClockGuard(soft_limit_sec=-1, warn_limit_sec=10, hard_limit_sec=20)
