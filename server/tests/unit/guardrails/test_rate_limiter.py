"""rate_limiter 单测."""

from __future__ import annotations

import asyncio

import pytest

from forge.guardrails.tool.rate_limiter import RateLimiter


@pytest.mark.asyncio
async def test_allows_under_limit() -> None:
    rl = RateLimiter(window_seconds=60, default_limit=5)
    for _ in range(5):
        r = await rl.check("shell", "developer")
        assert r.allow is True


@pytest.mark.asyncio
async def test_blocks_over_limit() -> None:
    rl = RateLimiter(window_seconds=60, default_limit=3)
    for _ in range(3):
        await rl.check("shell", "developer")
    r = await rl.check("shell", "developer")
    assert r.allow is False
    assert r.retry_after > 0
    assert "上限" in r.reason


@pytest.mark.asyncio
async def test_dimension_isolation() -> None:
    """(tool, role) 维度独立."""
    rl = RateLimiter(window_seconds=60, default_limit=1)
    assert (await rl.check("shell", "dev")).allow is True
    # 同工具不同角色 不受影响
    assert (await rl.check("shell", "qa")).allow is True
    # 不同工具同角色 不受影响
    assert (await rl.check("read_file", "dev")).allow is True
    # 同工具同角色 再来一次 -> 拦
    assert (await rl.check("shell", "dev")).allow is False


@pytest.mark.asyncio
async def test_window_sliding() -> None:
    """窗口外的旧记录应被丢弃, 重新放行."""
    rl = RateLimiter(window_seconds=0.05, default_limit=2)
    assert (await rl.check("shell", "dev")).allow is True
    assert (await rl.check("shell", "dev")).allow is True
    assert (await rl.check("shell", "dev")).allow is False
    await asyncio.sleep(0.08)
    assert (await rl.check("shell", "dev")).allow is True


@pytest.mark.asyncio
async def test_default_limit_applies_without_per_tool_config() -> None:
    """生产 get_rate_limiter() 工厂: 无 per-tool 配置时一律走默认上限."""
    from forge.guardrails.tool.rate_limiter import (
        get_rate_limiter,
        reset_rate_limiter,
    )

    reset_rate_limiter()
    try:
        rl = get_rate_limiter()
        assert rl.limit_for("knowledge_search") == 600  # default
        assert rl.limit_for("calculator") == 600  # default
    finally:
        reset_rate_limiter()


@pytest.mark.asyncio
async def test_custom_limits_override() -> None:
    rl = RateLimiter(tool_limits={"shell": 2, "calculator": 100})
    assert rl.limit_for("shell") == 2
    assert rl.limit_for("calculator") == 100
    assert (await rl.check("shell", "dev")).allow is True
    assert (await rl.check("shell", "dev")).allow is True
    assert (await rl.check("shell", "dev")).allow is False
