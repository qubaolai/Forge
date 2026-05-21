"""基于硬规则的 Router. 不依赖运行时统计.

决策树:
    1. requires_vision → 只保留 capabilities.supports_vision=True
    2. requires_tools → 只保留 capabilities.supports_tools=True
    3. requires_thinking → 只保留 capabilities.supports_thinking=True
    4. estimated_input_tokens > capabilities.context_window → 过滤掉
    5. preferred_provider / preferred_model 存在且在候选里 → 直接返回它
    6. task_type == "summary" → 优先 cost_tier="cheap"
    7. 其余 → 返回过滤后第一个候选 (顺序由 settings.llm.providers 决定)
"""

from __future__ import annotations

import logging

from .base import Candidate, Router, RoutingDecision, RoutingRequest

logger = logging.getLogger(__name__)


class RuleBasedRouter(Router):
    """基于声明式能力做硬筛选."""

    def route(
        self,
        request: RoutingRequest,
        available: list[Candidate],
    ) -> RoutingDecision | None:
        if not available:
            return None

        candidates: list[Candidate] = list(available)
        if request.requires_vision:
            candidates = [(p, m) for p, m in candidates if m.capabilities.supports_vision]
        if request.requires_tools:
            candidates = [(p, m) for p, m in candidates if m.capabilities.supports_tools]
        if request.requires_thinking:
            candidates = [(p, m) for p, m in candidates if m.capabilities.supports_thinking]
        if request.estimated_input_tokens > 0:
            candidates = [
                (p, m)
                for p, m in candidates
                if m.capabilities.context_window >= request.estimated_input_tokens
            ]

        if not candidates:
            logger.warning(
                "RuleBasedRouter: 过滤后无候选 (vision=%s tools=%s thinking=%s tokens=%d)",
                request.requires_vision,
                request.requires_tools,
                request.requires_thinking,
                request.estimated_input_tokens,
            )
            return None

        # 用户显式 pin (前端 model_options 给的 hint, 通常已经在外层短路了,
        # 这里兜底支持 router 内部 hint).
        if request.preferred_provider or request.preferred_model:
            for p, m in candidates:
                if request.preferred_provider in (None, p) and request.preferred_model in (
                    None,
                    m.name,
                ):
                    return RoutingDecision(
                        provider=p,
                        model=m.name,
                        reason="rule:preferred",
                    )

        # 摘要类任务优先 cheap
        if request.task_type == "summary":
            cheap = [c for c in candidates if c[1].capabilities.cost_tier == "cheap"]
            if cheap:
                p, m = cheap[0]
                return RoutingDecision(
                    provider=p,
                    model=m.name,
                    reason="rule:summary_cheap",
                )

        # 兜底: 过滤后第一个 (保持 yaml 配置顺序的语义)
        p, m = candidates[0]
        return RoutingDecision(
            provider=p,
            model=m.name,
            reason="rule:default",
        )
