"""LLM 弹性模块: 熔断 + 重试 + (Phase 4) 舱壁隔离.

所有子模块都暴露 Strategy ABC, 默认实现是进程内版本.
Phase 6 切换 Redis-backed 实现时, 调用方代码零改动.
"""

from __future__ import annotations

from .bulkhead import BulkheadRejectError, ProviderBulkhead, get_bulkhead, set_bulkhead
from .circuit_breaker import (
    BreakerConfig,
    BreakerKey,
    BreakerState,
    CircuitBreaker,
    CircuitBreakerRegistry,
    CircuitBreakerStrategy,
    InProcessCircuitBreaker,
    get_breaker_registry,
    get_breaker_strategy,
)
from .retry import (
    KeywordRetryPolicy,
    RetryPolicy,
    RetryResult,
    call_with_retry,
    is_rate_limit,
    is_retryable,
    parse_retry_after,
)

__all__ = [
    # bulkhead
    "BulkheadRejectError",
    "ProviderBulkhead",
    "get_bulkhead",
    "set_bulkhead",
    # circuit breaker
    "BreakerConfig",
    "BreakerKey",
    "BreakerState",
    "CircuitBreaker",
    "CircuitBreakerRegistry",
    "CircuitBreakerStrategy",
    "InProcessCircuitBreaker",
    "get_breaker_registry",
    "get_breaker_strategy",
    # retry
    "KeywordRetryPolicy",
    "RetryPolicy",
    "RetryResult",
    "call_with_retry",
    "is_rate_limit",
    "is_retryable",
    "parse_retry_after",
]
