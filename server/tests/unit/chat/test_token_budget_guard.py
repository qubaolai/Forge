"""TokenBudgetGuard 单测."""

from __future__ import annotations

import pytest

from forge.chat.guards import TokenBudgetGuard
from forge.chat.guards.base import LoopState


def _state(tokens: int) -> LoopState:
    return LoopState(step_index=0, max_steps=50, accumulated_tokens=tokens)


@pytest.mark.asyncio
async def test_zero_tokens_no_guidance() -> None:
    guard = TokenBudgetGuard(budget=1000)
    assert await guard.before_step(_state(0)) is None


@pytest.mark.asyncio
async def test_below_hint_threshold_no_guidance() -> None:
    guard = TokenBudgetGuard(budget=1000, hint_at=0.7)
    assert await guard.before_step(_state(500)) is None  # 50%


@pytest.mark.asyncio
async def test_hint_at_70_percent() -> None:
    guard = TokenBudgetGuard(budget=1000, hint_at=0.7, warn_at=0.9, force_at=0.95)
    g = await guard.before_step(_state(750))  # 75%
    assert g is not None and g.severity == "hint"


@pytest.mark.asyncio
async def test_warn_at_90_percent() -> None:
    guard = TokenBudgetGuard(budget=1000, hint_at=0.7, warn_at=0.9, force_at=0.95)
    g = await guard.before_step(_state(900))  # 90%
    assert g is not None and g.severity == "warning"


@pytest.mark.asyncio
async def test_force_stop_at_95_percent() -> None:
    guard = TokenBudgetGuard(budget=1000, hint_at=0.7, warn_at=0.9, force_at=0.95)
    g = await guard.before_step(_state(960))  # 96%
    assert g is not None and g.severity == "force_stop"


def test_invalid_budget_rejected() -> None:
    with pytest.raises(ValueError):
        TokenBudgetGuard(budget=0)
    with pytest.raises(ValueError):
        TokenBudgetGuard(budget=1000, hint_at=0.9, warn_at=0.7, force_at=0.95)
