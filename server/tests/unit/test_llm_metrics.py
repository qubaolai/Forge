"""LLM metrics 软依赖测试.

prometheus_client 安装与否, 模块导入和所有 helper API 都不能崩.
集成验证 (fallback chain 触发后指标上报正确) 在真 prometheus_client 装好时跑.
"""

from __future__ import annotations

from forge.observability.metrics import llm_metrics


def test_helpers_do_not_raise_without_label_or_value():
    # 没装 prometheus_client 时所有 helper 都是 no-op
    # 装了时也只是真实 emit, 不应抛
    llm_metrics.record_request("openai", "gpt-4o", "success")
    llm_metrics.record_request("openai", "gpt-4o", "error")
    llm_metrics.record_request("openai", "gpt-4o", "circuit_open")
    llm_metrics.record_latency("openai", "gpt-4o", 0.5)
    llm_metrics.record_tokens("openai", "gpt-4o", 100, 50)
    llm_metrics.record_tokens("openai", "gpt-4o", 0, 0)  # 都为 0 不上报
    llm_metrics.record_cost("openai", "gpt-4o", "u1", 0.01)
    llm_metrics.record_cost("openai", "gpt-4o", "", 0.0)
    llm_metrics.set_breaker_state("openai", "gpt-4o", "closed")
    llm_metrics.set_breaker_state("openai", "gpt-4o", "half_open")
    llm_metrics.set_breaker_state("openai", "gpt-4o", "open")
    llm_metrics.set_breaker_state("openai", "gpt-4o", "unknown_state")
    llm_metrics.inc_budget_exceeded("u1")
    llm_metrics.inc_budget_exceeded("")


def test_is_available_returns_bool():
    assert isinstance(llm_metrics.is_available(), bool)
