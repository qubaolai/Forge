"""WallClockGuard: 墙钟超时引导收尾.

目的: 防止单 turn 用户等太久 (LLM 慢, tool 调用慢, 等等).
默认:
    soft_limit_sec  (120s) -> hint: 提示已耗时
    warn_limit_sec  (180s) -> warning: 强引导
    hard_limit_sec  (240s) -> force_stop: tool_choice="none"

注意:
    - 与 HTTP 客户端 / 反代的 timeout 配合考虑; 别让 force_stop 还没来得及收尾
      用户就被反代切断.
    - 思考模式 (DeepSeek thinking) 的步可能单步 30s+, 几步累积容易触发软上限.
"""

from __future__ import annotations

from forge.chat.guards.base import Guidance, LoopGuard, LoopState


class WallClockGuard(LoopGuard):
    DEFAULT_SOFT_LIMIT = 120.0
    DEFAULT_WARN_LIMIT = 180.0
    DEFAULT_HARD_LIMIT = 240.0

    def __init__(
        self,
        *,
        soft_limit_sec: float = DEFAULT_SOFT_LIMIT,
        warn_limit_sec: float = DEFAULT_WARN_LIMIT,
        hard_limit_sec: float = DEFAULT_HARD_LIMIT,
    ) -> None:
        if not 0 < soft_limit_sec < warn_limit_sec < hard_limit_sec:
            raise ValueError("墙钟阈值应满足 0 < soft < warn < hard")
        self._soft = soft_limit_sec
        self._warn = warn_limit_sec
        self._hard = hard_limit_sec

    async def before_step(self, state: LoopState) -> Guidance | None:
        elapsed = state.elapsed_seconds
        if elapsed <= 0:
            return None

        if elapsed >= self._hard:
            return Guidance(
                content=(
                    f"🛑 本轮已运行 {int(elapsed)} 秒. "
                    "立刻基于已有信息输出最终答复, 不要再调任何工具."
                ),
                severity="force_stop",
            )
        if elapsed >= self._warn:
            return Guidance(
                content=(f"⚠️ 本轮已运行 {int(elapsed)} 秒. 请立刻收尾, 避免被强制中断."),
                severity="warning",
            )
        if elapsed >= self._soft:
            return Guidance(
                content=(f"提示: 本轮已运行 {int(elapsed)} 秒. 请尽快收敛思路."),
                severity="hint",
            )
        return None
