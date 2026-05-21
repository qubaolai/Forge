"""StuckDetector 单测.

覆盖核心场景 + 用户提的"读多个不同文件不该误伤":
    1. 不同 args (read a.py / b.py / c.py) -> 永远不触发
    2. 中间插入不同调用打断连续 -> 不触发
    3. 连续 3 次相同 -> warning
    4. 连续 5 次相同 -> force_stop
    5. 单步没有 tool 调用 (last_tool_name=None) -> 不影响计数
    6. 跨 turn 不共享 (新实例 deque 空)
"""

from __future__ import annotations

import pytest

from forge.chat.guards import StuckDetector
from forge.chat.guards.base import LoopState


def _state(name: str | None, args_hash: str | None, step: int = 0) -> LoopState:
    return LoopState(
        step_index=step,
        max_steps=50,
        last_tool_name=name,
        last_tool_args_hash=args_hash,
    )


@pytest.mark.asyncio
async def test_different_args_never_triggers() -> None:
    """用户提的场景: 读多个不同文件, 工具名相同但 args 不同, 不该触发."""
    guard = StuckDetector(warn_at=3, force_at=5)
    for path_hash in ["aaa", "bbb", "ccc", "ddd", "eee"]:
        result = await guard.before_step(_state("read_file", path_hash))
        assert result is None


@pytest.mark.asyncio
async def test_interleaved_call_resets_counter() -> None:
    """read A -> edit A -> read A -> edit A -> read A: 不该 warning."""
    guard = StuckDetector(warn_at=3, force_at=5)
    sequence = [
        ("read_file", "hash_a"),
        ("edit_file", "hash_e"),
        ("read_file", "hash_a"),
        ("edit_file", "hash_e"),
        ("read_file", "hash_a"),
    ]
    for name, h in sequence:
        result = await guard.before_step(_state(name, h))
        # 任何一步都不该 warning/force_stop, 因为没有连续 3 个相同
        assert result is None


@pytest.mark.asyncio
async def test_three_consecutive_warns() -> None:
    guard = StuckDetector(warn_at=3, force_at=5)
    await guard.before_step(_state("read_file", "h1"))
    await guard.before_step(_state("read_file", "h1"))
    result = await guard.before_step(_state("read_file", "h1"))
    assert result is not None
    assert result.severity == "warning"
    assert "read_file" in result.content


@pytest.mark.asyncio
async def test_five_consecutive_force_stops() -> None:
    guard = StuckDetector(warn_at=3, force_at=5)
    for _ in range(5):
        result = await guard.before_step(_state("read_file", "h1"))
    assert result is not None
    assert result.severity == "force_stop"


@pytest.mark.asyncio
async def test_none_last_tool_does_not_count() -> None:
    """step 没调 tool (LLM 出文本直接结束): 不影响 deque."""
    guard = StuckDetector(warn_at=3, force_at=5)
    await guard.before_step(_state("read_file", "h1"))
    await guard.before_step(_state(None, None))  # 不计入 deque
    await guard.before_step(_state("read_file", "h1"))
    # 实际只入 deque 2 次相同, 不触发
    result = await guard.before_step(_state(None, None))
    assert result is None


@pytest.mark.asyncio
async def test_new_instance_does_not_share_state() -> None:
    """跨 turn 状态隔离: 新实例 deque 空."""
    g1 = StuckDetector(warn_at=3, force_at=5)
    for _ in range(5):
        await g1.before_step(_state("x", "h"))

    g2 = StuckDetector(warn_at=3, force_at=5)
    result = await g2.before_step(_state("x", "h"))
    assert result is None


def test_invalid_thresholds_rejected() -> None:
    with pytest.raises(ValueError):
        StuckDetector(warn_at=1)
    with pytest.raises(ValueError):
        StuckDetector(warn_at=5, force_at=3)
