"""LLM 调用 Prometheus 指标.

软依赖 prometheus_client: 没装时所有 inc/observe 都是 no-op, 不抛, 不影响业务.

指标清单:
    llm_requests_total{provider, model, status}        Counter
        status: success | error | circuit_open
    llm_latency_seconds{provider, model}               Histogram (首包/单次调用)
    llm_tokens_total{provider, model, token_type}      Counter
        token_type: prompt | completion
    llm_cost_usd_total{provider, model, user_id}       Counter
    circuit_breaker_state{provider, model}             Gauge (0=closed 1=half_open 2=open)
    llm_budget_exceeded_total{user_id}                 Counter

调用方式:
    from forge.observability.metrics.llm_metrics import (
        record_request, record_latency, record_tokens, record_cost,
        set_breaker_state, inc_budget_exceeded,
    )
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_counter_factory: Any
_gauge_factory: Any
_histogram_factory: Any

try:
    import prometheus_client as _prometheus_client

    _AVAILABLE = True
except ImportError:  # pragma: no cover
    _AVAILABLE = False

    # ── no-op fallback (与 prometheus_client API 形似) ──────────────────────
    class _Noop:
        def labels(self, *args: Any, **kwargs: Any) -> _Noop:
            return self

        def inc(self, *_: Any, **__: Any) -> None:
            pass

        def observe(self, *_: Any, **__: Any) -> None:
            pass

        def set(self, *_: Any, **__: Any) -> None:
            pass

    def _counter_factory(*_: Any, **__: Any) -> _Noop:
        return _Noop()

    def _gauge_factory(*_: Any, **__: Any) -> _Noop:
        return _Noop()

    def _histogram_factory(*_: Any, **__: Any) -> _Noop:
        return _Noop()
else:
    _counter_factory = _prometheus_client.Counter
    _gauge_factory = _prometheus_client.Gauge
    _histogram_factory = _prometheus_client.Histogram


# ── 指标定义 ─────────────────────────────────────────────────────────────────
llm_requests_total = _counter_factory(
    "llm_requests_total",
    "LLM 调用次数",
    labelnames=("provider", "model", "status"),
)
llm_latency_seconds = _histogram_factory(
    "llm_latency_seconds",
    "LLM 单次调用延迟 (首包或完整非流式)",
    labelnames=("provider", "model"),
    buckets=(0.1, 0.3, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
)
llm_tokens_total = _counter_factory(
    "llm_tokens_total",
    "LLM token 累计",
    labelnames=("provider", "model", "token_type"),
)
llm_cost_usd_total = _counter_factory(
    "llm_cost_usd_total",
    "LLM 估算成本 USD 累计 (per user)",
    labelnames=("provider", "model", "user_id"),
)
circuit_breaker_state = _gauge_factory(
    "circuit_breaker_state",
    "熔断器状态 (0=closed, 1=half_open, 2=open)",
    labelnames=("provider", "model"),
)
llm_budget_exceeded_total = _counter_factory(
    "llm_budget_exceeded_total",
    "LLM 预算触发次数",
    labelnames=("user_id",),
)
llm_cache_tokens_total = _counter_factory(
    "llm_cache_tokens_total",
    "LLM 服务端 prompt cache 命中 token 数 (Anthropic ephemeral / OpenAI / DeepSeek 自动)",
    labelnames=("provider", "model"),
)


def is_available() -> bool:
    return _AVAILABLE


# ── helper APIs (供 fallback.py / circuit_breaker.py / cost_tracker.py 调) ──
def record_request(provider: str, model: str, status: str) -> None:
    llm_requests_total.labels(provider=provider, model=model, status=status).inc()


def record_latency(provider: str, model: str, seconds: float) -> None:
    llm_latency_seconds.labels(provider=provider, model=model).observe(seconds)


def record_tokens(provider: str, model: str, prompt: int, completion: int) -> None:
    if prompt:
        llm_tokens_total.labels(provider=provider, model=model, token_type="prompt").inc(prompt)
    if completion:
        llm_tokens_total.labels(
            provider=provider,
            model=model,
            token_type="completion",
        ).inc(completion)


def record_cost(provider: str, model: str, user_id: str, usd: float) -> None:
    if usd > 0:
        llm_cost_usd_total.labels(
            provider=provider,
            model=model,
            user_id=user_id or "anon",
        ).inc(usd)


_STATE_VALUE = {"closed": 0, "half_open": 1, "open": 2}


def set_breaker_state(provider: str, model: str, state: str) -> None:
    circuit_breaker_state.labels(provider=provider, model=model).set(_STATE_VALUE.get(state, 0))


def inc_budget_exceeded(user_id: str) -> None:
    llm_budget_exceeded_total.labels(user_id=user_id or "anon").inc()


def record_cache_tokens(provider: str, model: str, cached: int) -> None:
    if cached > 0:
        llm_cache_tokens_total.labels(provider=provider, model=model).inc(cached)
