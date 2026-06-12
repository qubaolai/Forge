"""memory.policies NoOp 实现单测.

只验 Stage 2 的零行为契约:
    NoOpConflictResolver.resolve   -> 永远返回 Insert(content=new.content)
    NoForgetting.is_alive          -> 永远 True
    NoForgetting.should_prune      -> 永远 False
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

from forge.memory.base import Fact
from forge.memory.policies import (
    Insert,
    NoForgetting,
    NoOpConflictResolver,
)
from forge.memory.scope import MemoryScope


def _fact(content: str = "用户偏好 Python", **overrides) -> Fact:
    base: dict[str, Any] = {
        "id": "fact_001",
        "user_id": "user_abc",
        "content": content,
        "source": "llm_extracted",
    }
    base.update(overrides)
    return Fact(**base)


# ---------------------------------------------------------------------------
# NoOpConflictResolver
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_noop_resolver_returns_insert_with_no_existing() -> None:
    resolver = NoOpConflictResolver()
    new = _fact()
    result = await resolver.resolve(MemoryScope.for_user("user_abc"), new, [])
    assert isinstance(result, Insert)
    assert result.content == new.content


@pytest.mark.asyncio
async def test_noop_resolver_ignores_similar_existing() -> None:
    """即使语义上完全重复, NoOp 也不去重."""
    resolver = NoOpConflictResolver()
    new = _fact(content="用户偏好 Python")
    existing = [_fact(id="fact_old", content="用户偏好 Python", score=0.99)]
    result = await resolver.resolve(MemoryScope.for_user("user_abc"), new, existing)
    assert isinstance(result, Insert)
    assert result.content == "用户偏好 Python"


# ---------------------------------------------------------------------------
# NoForgetting
# ---------------------------------------------------------------------------
def test_no_forgetting_keeps_everything_alive() -> None:
    policy = NoForgetting()
    now = datetime(2026, 5, 17)
    fresh = _fact(created_at=now)
    ancient = _fact(created_at=now - timedelta(days=3650))
    assert policy.is_alive(fresh, now) is True
    assert policy.is_alive(ancient, now) is True


def test_no_forgetting_never_prunes() -> None:
    policy = NoForgetting()
    now = datetime(2026, 5, 17)
    fact = _fact(created_at=now - timedelta(days=3650))
    assert policy.should_prune(fact, now) is False
