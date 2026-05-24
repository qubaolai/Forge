"""LLM Router 包.

只决定 primary；运行时 provider/model 不再配置 fallback chain。
"""

from .base import Candidate, Router, RoutingDecision, RoutingRequest
from .composite import CompositeRouter, get_default_router
from .rule_based import RuleBasedRouter

__all__ = [
    "Candidate",
    "Router",
    "RoutingRequest",
    "RoutingDecision",
    "RuleBasedRouter",
    "CompositeRouter",
    "get_default_router",
]
