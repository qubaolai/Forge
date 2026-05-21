"""StepSafetyNet: 把 max_steps 从硬上限变成软引导.

之前的行为: 达到 max_steps 抛 AgentMaxStepsError / finish_reason=length, 用户
感知 "失败了".

现在:
    剩 N 步 (默认 N=3) -> hint:    "你还剩 N 步, 请尽快收尾"
    剩 1 步           -> force_stop: tool_choice="none", 强制 LLM 给最终答复

LLM 在最后一步即使想继续调工具, 也被迫输出文本. 配合 R5 (task_partial),
如果 LLM 在最后一步给出的是不完整答复, 用户能点 [继续生成] 接着跑.
"""

from __future__ import annotations

from forge.chat.guards.base import Guidance, LoopState


class StepSafetyNet:
    """无状态. 但符合 LoopGuard Protocol 还是按 turn 实例化习惯一致."""

    def __init__(self, warning_window: int = 3) -> None:
        """
        Args:
            warning_window: 剩多少步开始 hint 收尾. 默认 3.
        """
        self._warning_window = max(1, warning_window)

    async def before_step(self, state: LoopState) -> Guidance | None:
        remaining = state.max_steps - state.step_index
        if remaining <= 0:
            # 兜底 (理论上 ReActAgent 不会 over-run)
            return Guidance(
                content="你已达到最大步数, 本轮必须立刻给出文字答复, 不要再调任何工具.",
                severity="force_stop",
            )
        if remaining == 1:
            return Guidance(
                content=(
                    "⚠️ 这是你本轮的最后一步, 必须给出最终答复, 不要再调用任何工具.\n"
                    "如果信息不足以完美回答, 基于已有内容总结现状, 并说明哪些部分尚不确定."
                ),
                severity="force_stop",
            )
        if remaining <= self._warning_window:
            return Guidance(
                content=(
                    f"提示: 你本轮还剩 {remaining} 步预算 (含本步). "
                    "请尽快收敛, 避免在末尾被强制中断."
                ),
                severity="hint",
            )
        return None
