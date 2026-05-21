"""LLM Router 包.

只决定 primary; fallback chain 仍走 settings.llm.fallback_chain 静态配置.
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
