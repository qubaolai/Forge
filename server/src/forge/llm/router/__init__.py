"""LLM Router 包.

只决定 primary; 运行时 provider/model 不再配置 fallback chain.

Phase 5 完成的 Router:
    - RuleBasedRouter:    硬约束 + preferred pin 短路
    - CostAwareRouter:    预算压力 / 大输入降级 cheap tier
    - LatencyAwareRouter: EMA 延迟统计的软优先级
"""

from .ab_test import ABTestGroup, ABTestRouter
from .base import Candidate, Router, RoutingDecision, RoutingRequest
from .composite import CompositeRouter, get_default_router
from .cost_aware import CostAwareRouter
from .latency_aware import LatencyAwareRouter, get_latency_router
from .rule_based import RuleBasedRouter

__all__ = [
    "ABTestGroup",
    "ABTestRouter",
    "Candidate",
    "CompositeRouter",
    "CostAwareRouter",
    "LatencyAwareRouter",
    "Router",
    "RoutingDecision",
    "RoutingRequest",
    "RuleBasedRouter",
    "get_default_router",
    "get_latency_router",
]
