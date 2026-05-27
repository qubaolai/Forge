"""成本感知路由 (软约束).

决策树:
    1. preferred_provider/model 存在 → 不介入 (让用户 pin 生效)
    2. 用户当日已用预算 ≥ alert_threshold * limit → 强制 cheap tier
    3. estimated_input_tokens > large_input_threshold → 优先 cheap tier
    4. 否则不表态 (返回 None, 让后续 Router 决定)

仅对**系统自动路由**生效 (adaptive 任务节点 / utility chain); chat 路径用户已选模型时
RuleBasedRouter 会先返回 preferred, 这里不会被调到.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..cost_tracker import CostTracker, get_cost_tracker
from .base import Candidate, Router, RoutingDecision, RoutingRequest

logger = logging.getLogger(__name__)


@dataclass
class CostAwareRouter(Router):
    """基于 cost_tier 和用户剩余预算的软约束路由."""

    large_input_threshold: int = 20_000
    """输入 token 数超过此阈值时倾向 cheap tier (省钱)."""

    alert_threshold: float = 0.8
    """用户日用量达到该比例时强制 cheap tier."""

    cost_tracker: CostTracker | None = None

    def _tracker(self) -> CostTracker:
        return self.cost_tracker or get_cost_tracker()

    def route(
        self,
        request: RoutingRequest,
        available: list[Candidate],
    ) -> RoutingDecision | None:
        # 用户显式 pin: 不介入
        if request.preferred_provider or request.preferred_model:
            return None
        if not available:
            return None

        cheap = [c for c in available if c[1].capabilities.cost_tier == "cheap"]
        if not cheap:
            return None

        force_cheap = False
        reason_suffix = ""

        # 预算压力: 用户已用接近上限
        if request.user_id:
            tracker = self._tracker()
            limit = tracker.budget.limit_for(request.user_id)
            if limit is not None and limit > 0:
                try:
                    used = tracker._user_total(request.user_id)  # noqa: SLF001
                except Exception:  # noqa: BLE001
                    logger.debug("查询用户日用量失败, 忽略预算压力", exc_info=True)
                    used = 0.0
                if used >= self.alert_threshold * limit:
                    force_cheap = True
                    reason_suffix = f"budget_pressure({used:.4f}/{limit:.4f})"

        # 大输入降级到 cheap (省钱)
        if not force_cheap and request.estimated_input_tokens >= self.large_input_threshold:
            force_cheap = True
            reason_suffix = f"large_input({request.estimated_input_tokens})"

        if not force_cheap:
            return None

        p, m = cheap[0]
        return RoutingDecision(
            provider=p,
            model=m.name,
            reason=f"cost:{reason_suffix}",
        )


__all__ = ["CostAwareRouter"]
