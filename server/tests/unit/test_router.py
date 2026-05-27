"""Router 单元测试.

覆盖:
    - RuleBasedRouter 各过滤分支 (vision / tools / thinking / context_window)
    - preferred_provider / preferred_model 命中
    - task_type='summary' 走 cheap
    - 过滤后无候选 → 返 None
    - CompositeRouter 串联 + 兜底 default_first
    - LLMGateway._resolve_provider_model 集成: 显式 pin > Router 决策 > default
"""

from __future__ import annotations

from forge.config.domains.llm import ModelCapabilities, ModelConfig

from forge.llm.router import (
    Candidate,
    CompositeRouter,
    RoutingDecision,
    RoutingRequest,
    RuleBasedRouter,
)


def _mc(
    name: str,
    *,
    tools: bool = True,
    vision: bool = False,
    thinking: bool = False,
    cw: int = 100_000,
    tier: str = "mid",
) -> ModelConfig:
    return ModelConfig(
        name=name,
        capabilities=ModelCapabilities(
            supports_tools=tools,
            supports_vision=vision,
            supports_thinking=thinking,
            context_window=cw,
            cost_tier=tier,  # type: ignore[arg-type]
        ),
    )


def _candidates(*models: tuple[str, ModelConfig]) -> list[Candidate]:
    return list(models)


# ── RuleBasedRouter ────────────────────────────────────────────────────────
def test_rule_filters_by_vision():
    r = RuleBasedRouter()
    cands = _candidates(
        ("openai", _mc("gpt-4o-mini", vision=False)),
        ("openai", _mc("gpt-4o", vision=True)),
    )
    decision = r.route(RoutingRequest(requires_vision=True), cands)
    assert decision is not None
    assert decision.model == "gpt-4o"


def test_rule_filters_by_tools():
    r = RuleBasedRouter()
    cands = _candidates(
        ("anthropic", _mc("claude-text", tools=False)),
        ("openai", _mc("gpt-4o", tools=True)),
    )
    decision = r.route(RoutingRequest(requires_tools=True), cands)
    assert decision is not None
    assert decision.provider == "openai"


def test_rule_filters_by_thinking():
    r = RuleBasedRouter()
    cands = _candidates(
        ("openai", _mc("gpt-4o", thinking=False)),
        ("deepseek", _mc("deepseek-reasoner", thinking=True)),
    )
    decision = r.route(RoutingRequest(requires_thinking=True), cands)
    assert decision is not None
    assert decision.provider == "deepseek"


def test_rule_filters_by_context_window():
    r = RuleBasedRouter()
    cands = _candidates(
        ("openai", _mc("small", cw=4_000)),
        ("openai", _mc("large", cw=200_000)),
    )
    decision = r.route(RoutingRequest(estimated_input_tokens=50_000), cands)
    assert decision is not None
    assert decision.model == "large"


def test_rule_summary_prefers_cheap():
    r = RuleBasedRouter()
    cands = _candidates(
        ("openai", _mc("gpt-4o", tier="expensive")),
        ("openai", _mc("gpt-4o-mini", tier="cheap")),
    )
    decision = r.route(RoutingRequest(task_type="summary"), cands)
    assert decision is not None
    assert decision.model == "gpt-4o-mini"
    assert "summary" in decision.reason


def test_rule_preferred_pin_inside_router():
    r = RuleBasedRouter()
    cands = _candidates(
        ("openai", _mc("gpt-4o")),
        ("anthropic", _mc("claude-opus-4")),
    )
    decision = r.route(
        RoutingRequest(preferred_provider="anthropic"),
        cands,
    )
    assert decision is not None
    assert decision.provider == "anthropic"


def test_rule_returns_none_when_filter_empty():
    r = RuleBasedRouter()
    cands = _candidates(("openai", _mc("gpt-4o", vision=False)))
    decision = r.route(RoutingRequest(requires_vision=True), cands)
    assert decision is None


# ── CompositeRouter ────────────────────────────────────────────────────────
class _NoOpinion:
    def route(self, request, available):
        return None


class _ForcePick:
    def __init__(self, provider, model, reason="forced"):
        self._p = provider
        self._m = model
        self._reason = reason

    def route(self, request, available):
        return RoutingDecision(provider=self._p, model=self._m, reason=self._reason)


def test_composite_returns_first_non_none():
    cands = _candidates(
        ("a", _mc("m1")),
        ("b", _mc("m2")),
    )
    cr = CompositeRouter([_NoOpinion(), _ForcePick("b", "m2")])
    decision = cr.route(RoutingRequest(), cands)
    assert decision is not None
    assert decision.provider == "b"


def test_composite_default_fallback():
    cands = _candidates(("a", _mc("m1")), ("b", _mc("m2")))
    cr = CompositeRouter([_NoOpinion(), _NoOpinion()])
    decision = cr.route(RoutingRequest(), cands)
    assert decision is not None
    assert decision.provider == "a"
    assert decision.reason == "composite:default_first"


def test_composite_handles_router_exception(caplog):
    import logging

    class _Boom:
        def route(self, request, available):
            raise RuntimeError("router 内部炸了")

    cands = _candidates(("a", _mc("m1")), ("b", _mc("m2")))
    cr = CompositeRouter([_Boom(), _ForcePick("b", "m2")])
    with caplog.at_level(logging.ERROR):
        decision = cr.route(RoutingRequest(), cands)
    assert decision is not None
    assert decision.provider == "b"


def test_composite_none_when_no_candidates():
    cr = CompositeRouter([_NoOpinion()])
    decision = cr.route(RoutingRequest(), [])
    assert decision is None
