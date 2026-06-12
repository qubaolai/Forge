"""ThresholdDedupResolver 单测: 阈值边界行为."""

from __future__ import annotations

import pytest

from forge.memory.base import Fact
from forge.memory.policies.conflict import Insert, Skip, ThresholdDedupResolver
from forge.memory.scope import MemoryScope

SCOPE = MemoryScope.for_user("u1")


def _fact(score: float) -> Fact:
    return Fact(id="f1", user_id="u1", content="已有事实", source="llm_extracted", score=score)


def _new() -> Fact:
    return Fact(id="", user_id="u1", content="新事实", source="llm_extracted")


@pytest.mark.asyncio
async def test_no_similar_inserts() -> None:
    resolution = await ThresholdDedupResolver(0.92).resolve(SCOPE, _new(), [])
    assert isinstance(resolution, Insert)
    assert resolution.content == "新事实"


@pytest.mark.asyncio
async def test_below_threshold_inserts() -> None:
    resolution = await ThresholdDedupResolver(0.92).resolve(SCOPE, _new(), [_fact(0.91)])
    assert isinstance(resolution, Insert)


@pytest.mark.asyncio
async def test_at_threshold_skips() -> None:
    resolution = await ThresholdDedupResolver(0.92).resolve(SCOPE, _new(), [_fact(0.92)])
    assert isinstance(resolution, Skip)
    assert "dup" in resolution.reason


@pytest.mark.asyncio
async def test_uses_max_score_among_similar() -> None:
    similar = [_fact(0.3), _fact(0.95), _fact(0.5)]
    resolution = await ThresholdDedupResolver(0.92).resolve(SCOPE, _new(), similar)
    assert isinstance(resolution, Skip)
