"""StuckDetector: 连续相同 tool 调用 -> 引导 / 强制收尾.

设计要点 (避免误伤合法场景):
    - 检测键 = (tool_name, args_canonical_hash). 不同 args 视为不同调用.
    - 只看 "连续" N 次完全相同; 中间任何不同调用都重置计数.
    - 触发后注入 system 引导, 不立刻强制 (给 LLM 一次反应机会), 再来一次同样的
      才 force_stop.

场景对照:
    read_file("a.py") -> read_file("b.py") -> read_file("c.py")
        ✓ 不同 args -> 不触发 (用户合法读取多文件)
    read_file("a.py") -> edit_file(...) -> read_file("a.py")
        ✓ 中间有 edit 插入 -> 不触发 (合理的 read-modify-verify)
    read_file("a.py") * 3 连续
        ⚠ warn -> hint "你已连续 3 次"
    read_file("a.py") * 5 连续
        🛑 force_stop -> tool_choice="none"
"""

from __future__ import annotations

from collections import deque

from forge.chat.guards.base import Guidance, LoopState


class StuckDetector:
    """无跨 turn 共享. Runner 每 turn 构造一次."""

    def __init__(self, *, warn_at: int = 3, force_at: int = 5) -> None:
        if warn_at < 2:
            raise ValueError("warn_at 至少为 2")
        if force_at < warn_at:
            raise ValueError("force_at 必须 >= warn_at")
        self._warn_at = warn_at
        self._force_at = force_at
        # 只存最近 force_at 次的 (name, args_hash); deque 自动滚动
        self._recent: deque[tuple[str, str]] = deque(maxlen=force_at)

    async def before_step(self, state: LoopState) -> Guidance | None:
        # 把上一步的 tool 调用加进队列 (state.last_tool_* 来自 Runner)
        if state.last_tool_name:
            self._recent.append((state.last_tool_name, state.last_tool_args_hash or ""))

        if not self._recent:
            return None

        # 看连续相同次数 (从右往左)
        run_len = self._consecutive_identical_run()

        if run_len >= self._force_at:
            tool, _ = self._recent[-1]
            return Guidance(
                content=(
                    f"🛑 你已连续 {run_len} 次以完全相同的参数调用 `{tool}`, "
                    "明显陷入循环. 立刻基于已有信息给出最终答复, 不要再调任何工具."
                ),
                severity="force_stop",
            )
        if run_len >= self._warn_at:
            tool, _ = self._recent[-1]
            return Guidance(
                content=(
                    f"⚠️ 你已连续 {run_len} 次以完全相同的参数调用 `{tool}`. "
                    "请基于已有结果继续推进, 不要重复调用; 若信息确实不足, 改换思路或参数."
                ),
                severity="warning",
            )
        return None

    def _consecutive_identical_run(self) -> int:
        """从队尾起, 连续相同的次数."""
        if not self._recent:
            return 0
        last = self._recent[-1]
        count = 0
        for item in reversed(self._recent):
            if item == last:
                count += 1
            else:
                break
        return count
