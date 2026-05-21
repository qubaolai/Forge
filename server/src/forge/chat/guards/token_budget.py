"""TokenBudgetGuard: 累计 token 接近预算时引导收尾.

目的:
    - 成本控制 (token = 钱)
    - 防止 "无限循环但每步消耗低" 这类绕过 StuckDetector 的情况

阈值:
    hint_at  (默认 70%) -> hint: 提示已耗多少
    warn_at  (默认 90%) -> warning: 强引导收尾
    force_at (默认 95%) -> force_stop: tool_choice="none"

默认预算 100k token. 对小模型 (4k 窗口) 远超合理; 对长上下文模型 (128k) 也够
跑十几步. 可按 Agent 配置覆盖.
"""

from __future__ import annotations

from forge.chat.guards.base import Guidance, LoopState


class TokenBudgetGuard:
    DEFAULT_BUDGET = 100_000

    def __init__(
        self,
        *,
        budget: int = DEFAULT_BUDGET,
        hint_at: float = 0.7,
        warn_at: float = 0.9,
        force_at: float = 0.95,
    ) -> None:
        if budget <= 0:
            raise ValueError("budget 必须为正")
        if not 0.0 < hint_at < warn_at < force_at <= 1.0:
            raise ValueError("阈值应满足 0 < hint < warn < force <= 1.0")
        self._budget = budget
        self._hint_at = hint_at
        self._warn_at = warn_at
        self._force_at = force_at

    async def before_step(self, state: LoopState) -> Guidance | None:
        if state.accumulated_tokens <= 0:
            return None
        ratio = state.accumulated_tokens / self._budget
        used_pct = int(ratio * 100)

        if ratio >= self._force_at:
            return Guidance(
                content=(
                    f"🛑 已消耗 {used_pct}% 的 token 预算 "
                    f"({state.accumulated_tokens}/{self._budget}). "
                    "立刻基于已有信息输出最终答复, 不要再调任何工具."
                ),
                severity="force_stop",
            )
        if ratio >= self._warn_at:
            return Guidance(
                content=(
                    f"⚠️ 已消耗 {used_pct}% 的 token 预算 "
                    f"({state.accumulated_tokens}/{self._budget}). "
                    "请立刻收尾输出最终答复, 避免被强制中断."
                ),
                severity="warning",
            )
        if ratio >= self._hint_at:
            return Guidance(
                content=(
                    f"提示: 已消耗 {used_pct}% 的 token 预算. 请优先收敛思路, 减少不必要的工具调用."
                ),
                severity="hint",
            )
        return None
