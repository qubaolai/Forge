"""向后兼容重导出: 旧路径 forge.llm.circuit_breaker → forge.llm.resilience.circuit_breaker.

新代码请使用 forge.llm.resilience.
"""

from __future__ import annotations

from .resilience.circuit_breaker import (
    BreakerConfig,
    BreakerKey,
    BreakerOutcome,
    BreakerState,
    CircuitBreaker,
    CircuitBreakerRegistry,
    CircuitBreakerStrategy,
    InProcessCircuitBreaker,
    get_breaker_registry,
    get_breaker_strategy,
)

__all__ = [
    "BreakerConfig",
    "BreakerKey",
    "BreakerOutcome",
    "BreakerState",
    "CircuitBreaker",
    "CircuitBreakerRegistry",
    "CircuitBreakerStrategy",
    "InProcessCircuitBreaker",
    "get_breaker_registry",
    "get_breaker_strategy",
]
