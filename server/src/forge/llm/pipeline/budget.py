"""预算 / 限额 Pre 中间件.

职责:
    - 网关级 pre-flight 检查: 调用 cost_tracker.check_budget 看用户是否还有预算
    - 与 dispatcher 内 per-spec quota_controlled 检查互补:
        * 这里: 默认 quota_controlled=False, 仅查 daily-USD 全局/用户上限
        * dispatcher: 按 spec.quota_controlled 决定是否查 user_quota (多维滚动窗口)
      Phase 6 会统一为 QuotaStrategy.

超额时抛 LLMBudgetExceeded, dispatcher 的 retry 不会重试它.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..cost_tracker import CostTracker, get_cost_tracker
from ..request import LLMRequest, LLMResponse
from .base import PreMiddleware

logger = logging.getLogger(__name__)


@dataclass
class BudgetMiddleware(PreMiddleware):
    """预算 pre-flight 检查.

    若超额则抛 `LLMBudgetExceeded`, 由 LLMGateway 上层捕获并返回业务错误.
    """

    cost_tracker: CostTracker | None = field(default=None)
    """注入式 cost tracker. None 时使用全局单例 (默认行为)."""

    def _tracker(self) -> CostTracker:
        return self.cost_tracker or get_cost_tracker()

    async def process(self, req: LLMRequest) -> LLMResponse | None:
        tracker = self._tracker()
        # 网关级只查 daily-USD 上限; 多维滚动窗口 (user_quota) 由 dispatcher 内部
        # 按 spec.quota_controlled 决定是否调用. 因此这里 quota_controlled=False.
        tracker.check_budget(user_id=req.user_id, quota_controlled=False)
        return None


__all__ = ["BudgetMiddleware"]
