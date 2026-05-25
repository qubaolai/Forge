"""BudgetPolicy: 从 ContextRequest 计算 WindowBudget.

按 ContextMode 给不同的预算比例:
    CHAT:     system 15% / dialogue 40% / tool_result 15% (剩 30% 输出预算)
    TASK:     system 20% / dialogue  0% / tool_result 50% (剩 30%)
    WORKFLOW: system 20% / dialogue 15% / tool_result 35% (剩 30%)
"""

from __future__ import annotations

from forge.context_mgmt.types import ContextMode, ContextRequest, WindowBudget


class DefaultBudgetPolicy:
    """按 mode 给固定比例的预算分配策略."""

    # mode -> (system, dialogue, tool_result) 比例
    _RATIOS: dict[ContextMode, tuple[float, float, float]] = {
        ContextMode.CHAT:     (0.15, 0.40, 0.15),
        ContextMode.TASK:     (0.20, 0.00, 0.50),
        ContextMode.WORKFLOW: (0.20, 0.15, 0.35),
    }

    def allocate(self, request: ContextRequest) -> WindowBudget:
        sys_r, dlg_r, tr_r = self._RATIOS.get(
            request.mode, self._RATIOS[ContextMode.CHAT]
        )
        w = request.context_window
        budget = WindowBudget(
            context_window=w,
            system_budget=int(w * sys_r),
            dialogue_budget=int(w * dlg_r),
            tool_result_budget=int(w * tr_r),
        )
        budget.validate()
        return budget
