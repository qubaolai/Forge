"""验证 DefaultContextBuilder 的 Fork-Join 并行特性.

PromptRenderer 和 ContentGatherer 必须并行执行 (asyncio.gather),
而不是串行. 注入有意延迟的 mock, 断言总耗时接近最大单边耗时,
而不是两者之和.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from forge.context_mgmt.budget.policy import DefaultBudgetPolicy
from forge.context_mgmt.builder.content_gatherer import (
    ContentGatherer,
    GatherResult,
)
from forge.context_mgmt.builder.context_builder import DefaultContextBuilder
from forge.context_mgmt.builder.message_assembler import MessageAssembler
from forge.context_mgmt.builder.prompt_renderer import PromptRenderer
from forge.context_mgmt.meter.token_meter import DefaultTokenMeter
from forge.context_mgmt.types import ContextMode, ContextRequest
from forge.llm.token_counter import HeuristicCounter


class _SlowRenderer(PromptRenderer):
    """渲染时人为 sleep delay 秒."""

    def __init__(self, delay: float) -> None:
        self._delay = delay

    async def render(self, request):
        await asyncio.sleep(self._delay)
        return "system prompt"


class _SlowGatherer:
    """gather 时人为 sleep delay 秒."""

    def __init__(self, delay: float) -> None:
        self._delay = delay

    async def gather(self, request):
        await asyncio.sleep(self._delay)
        return GatherResult(chunks={}, degraded=[])


@pytest.mark.asyncio
async def test_render_and_gather_run_in_parallel():
    """总耗时 ≈ max(render_delay, gather_delay), 不是两者之和."""
    render_delay = 0.10
    gather_delay = 0.20

    builder = DefaultContextBuilder(
        renderer=_SlowRenderer(render_delay),
        gatherer=_SlowGatherer(gather_delay),
        assembler=MessageAssembler(DefaultTokenMeter(HeuristicCounter())),
        budget_policy=DefaultBudgetPolicy(),
    )

    request = ContextRequest(
        user_id="u1", session_id="s1", current_user_message="hi"
    )

    start = time.perf_counter()
    await builder.build(request)
    elapsed = time.perf_counter() - start

    # 并行: 应该接近 0.20s. 给点宽容度允许调度抖动.
    # 串行的话会是 0.30s, 这里阈值 0.28s 足够区分.
    assert elapsed < 0.28, (
        f"并行未生效: 耗时 {elapsed:.3f}s 接近串行 (render+gather={render_delay+gather_delay:.3f}s)"
    )
    assert elapsed >= max(render_delay, gather_delay) * 0.95, (
        f"耗时 {elapsed:.3f}s 异常 (低于 max(render, gather))"
    )
