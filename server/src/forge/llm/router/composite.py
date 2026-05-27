"""CompositeRouter: 按优先级串联多个 Router, 第一个非 None 决策即采用.

默认链 (Phase 5):
    [RuleBasedRouter, CostAwareRouter, LatencyAwareRouter]

    - RuleBasedRouter: 硬约束筛选 + preferred_provider/model pin 短路
    - CostAwareRouter: 预算压力 / 大输入时强制 cheap tier
    - LatencyAwareRouter: 基于 EMA 延迟选最优 (软优先级降权)
    - 全部 None 时兜底用 available 第一个候选
"""

from __future__ import annotations

import logging

from .base import Candidate, Router, RoutingDecision, RoutingRequest

logger = logging.getLogger(__name__)


class CompositeRouter(Router):
    """串联 Router. 全部返 None 时, 兜底用 available 的第一个候选."""

    def __init__(self, routers: list[Router]) -> None:
        self._routers = list(routers)

    def route(
        self,
        request: RoutingRequest,
        available: list[Candidate],
    ) -> RoutingDecision | None:
        for r in self._routers:
            try:
                decision = r.route(request, available)
            except Exception:  # noqa: BLE001
                logger.exception("Router %s 决策失败, 跳过", type(r).__name__)
                continue
            if decision is not None:
                return decision

        if not available:
            return None
        p, m = available[0]
        return RoutingDecision(provider=p, model=m.name, reason="composite:default_first")


_default: CompositeRouter | None = None


def get_default_router() -> CompositeRouter:
    """全局默认 CompositeRouter 单例.

    Phase 5: [RuleBased → CostAware → LatencyAware]
    """
    global _default
    if _default is None:
        from .cost_aware import CostAwareRouter
        from .latency_aware import get_latency_router
        from .rule_based import RuleBasedRouter

        _default = CompositeRouter(
            [
                RuleBasedRouter(),
                CostAwareRouter(),
                get_latency_router(),
            ]
        )
    return _default
