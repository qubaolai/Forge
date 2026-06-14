"""BudgetPolicy: 从 ContextRequest 计算 WindowBudget.

固定的输入预算比例:
    system 15% / dialogue 40% / tool_result 15% (剩 30% 输出预算)
"""

from __future__ import annotations

from forge.context_mgmt.protocols import BudgetPolicy
from forge.context_mgmt.types import ContextRequest, WindowBudget


class DefaultBudgetPolicy(BudgetPolicy):
    """固定比例的预算分配策略."""

    # (system, dialogue, tool_result) 比例
    _RATIO: tuple[float, float, float] = (0.15, 0.40, 0.15)

    def allocate(self, request: ContextRequest) -> WindowBudget:
        sys_r, dlg_r, tr_r = self._RATIO
        w = request.context_window
        budget = WindowBudget(
            context_window=w,
            system_budget=int(w * sys_r),
            dialogue_budget=int(w * dlg_r),
            tool_result_budget=int(w * tr_r),
        )
        budget.validate()
        return budget
