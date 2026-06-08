"""CircuitBreaker 单元测试.

覆盖:
    - CLOSED → OPEN (达到阈值)
    - OPEN → HALF_OPEN (基于时间)
    - HALF_OPEN → CLOSED (探测成功)
    - HALF_OPEN → OPEN (探测失败)
    - 滑动窗口 evict 行为
    - Registry 单例 + 不同 key 隔离
"""

from __future__ import annotations

import time

from forge.llm.resilience.circuit_breaker import (
    BreakerConfig,
    BreakerState,
    CircuitBreaker,
    CircuitBreakerRegistry,
    get_breaker_registry,
)


def test_closed_to_open_when_failures_exceed_threshold():
    b = CircuitBreaker(BreakerConfig(failure_threshold=3, window_seconds=60))
    assert b.state == BreakerState.CLOSED
    b.record_failure()
    b.record_failure()
    assert b.state == BreakerState.CLOSED
    b.record_failure()
    assert b.state == BreakerState.OPEN
    assert b.is_open() is True


def test_open_to_half_open_after_timeout():
    b = CircuitBreaker(BreakerConfig(failure_threshold=1, half_open_after=0.05))
    b.record_failure()
    assert b.state == BreakerState.OPEN
    assert b.is_open() is True
    time.sleep(0.07)
    # is_open() 触发 OPEN → HALF_OPEN 转换并放行探测
    assert b.is_open() is False
    assert b.state == BreakerState.HALF_OPEN
    # HALF_OPEN 下 probe_count=1 已经被消费, 再问就拒绝
    assert b.is_open() is True


def test_half_open_to_closed_on_probe_success():
    b = CircuitBreaker(BreakerConfig(failure_threshold=3, half_open_after=0.01))
    b.record_failure()
    b.record_failure()
    b.record_failure()
    assert b.state == BreakerState.OPEN
    time.sleep(0.02)
    assert b.is_open() is False  # 放行探测
    b.record_success()
    assert b.state == BreakerState.CLOSED
    # 探测成功后 records 应被清空, 单次失败不应触发 OPEN
    b.record_failure()
    assert b.state == BreakerState.CLOSED


def test_half_open_to_open_on_probe_failure():
    b = CircuitBreaker(BreakerConfig(failure_threshold=1, half_open_after=0.01))
    b.record_failure()
    time.sleep(0.02)
    assert b.is_open() is False
    b.record_failure()
    assert b.state == BreakerState.OPEN
    assert b.is_open() is True


def test_window_evicts_old_failures():
    """旧失败超出窗口后应该不计入阈值."""
    b = CircuitBreaker(BreakerConfig(failure_threshold=3, window_seconds=0.05))
    b.record_failure()
    b.record_failure()
    time.sleep(0.07)  # 前两次失败应该出窗口
    b.record_failure()
    # 当前窗口内只有 1 次失败, 没到阈值
    assert b.state == BreakerState.CLOSED


def test_registry_returns_same_breaker_for_same_key():
    reg = CircuitBreakerRegistry()
    a1 = reg.get("openai", "sk-x", "gpt-4o")
    a2 = reg.get("openai", "sk-x", "gpt-4o")
    assert a1 is a2


def test_registry_isolates_different_keys():
    reg = CircuitBreakerRegistry(BreakerConfig(failure_threshold=1))
    a = reg.get("openai", "sk-x", "gpt-4o")
    b = reg.get("openai", "sk-y", "gpt-4o")  # 不同 api_key
    a.record_failure()
    assert a.state == BreakerState.OPEN
    assert b.state == BreakerState.CLOSED


def test_registry_shares_breaker_across_models_for_same_key():
    reg = CircuitBreakerRegistry()
    a = reg.get("openai", "sk-x", "gpt-4o")
    b = reg.get("openai", "sk-x", "gpt-3.5")
    assert a is b


def test_strategy_accepts_dispatcher_key_shape():
    reg = CircuitBreakerRegistry(BreakerConfig(failure_threshold=1))
    key = ("openai", "sk-x")
    assert reg.is_open(key) is False
    reg.record_failure(key)
    assert reg.is_open(key) is True


def test_global_registry_singleton():
    assert get_breaker_registry() is get_breaker_registry()


def test_snapshot_masks_api_key():
    reg = CircuitBreakerRegistry()
    reg.get("openai", "sk-abc123def", "gpt-4o")
    snap = reg.snapshot()
    assert any("sk-abc" in k and "***" in k for k in snap)
    # 完整 key 不应出现
    assert not any(k.split(":")[1] == "sk-abc123def" for k in snap)


def test_reset_clears_state():
    b = CircuitBreaker(BreakerConfig(failure_threshold=1))
    b.record_failure()
    assert b.state == BreakerState.OPEN
    b.reset()
    assert b.state == BreakerState.CLOSED
