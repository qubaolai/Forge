"""LLM 调用熔断器: provider 级时间窗口熔断.

为什么需要熔断:
    retry.py 只做"请求内重试" (同一 provider 内多次尝试).
    没有熔断时, primary provider 完全宕机 → 每个请求都要等 timeout (30s) 后才能 fallback,
    首包延迟雪崩到 30s+. 熔断器记忆"这个 provider 最近大量失败", 直接跳过, 0 等待切下一个.

状态机:
    CLOSED → (窗口内失败 ≥ threshold) → OPEN
       ↑                                  ↓ (half_open_after 秒后)
       └───────── HALF_OPEN ←────────────┘
                 (probe 成功→CLOSED / 失败→OPEN)

Key 粒度: (impl, api_key)
    - 同 impl 不同 api_key 视为不同熔断单元: 一个坏 key 不让所有 key 都熔

Strategy 抽象 (Phase 6 切 Redis):
    - CircuitBreakerStrategy: 调用方依赖的抽象, 决定"这个 key 当前是否要短路"
    - InProcessCircuitBreaker: 默认实现, 进程内 state (重启重置)
    - 后续 RedisCircuitBreaker (Phase 6) 继承同一 ABC, 状态存 Redis Hash + TTL
      多实例共享熔断状态, 上层 LLMDispatcher 调用代码零改动.
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

logger = logging.getLogger(__name__)


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True)
class BreakerConfig:
    """熔断器配置. 默认值偏保守, 生产可按 provider 实际质量调整."""

    failure_threshold: int = 5
    """滑动窗口内失败次数 ≥ 此值触发熔断."""

    window_seconds: float = 60.0
    """统计窗口长度 (秒). 窗口外的失败/成功记录会被丢弃."""

    half_open_after: float = 30.0
    """OPEN 状态持续多少秒后转 HALF_OPEN 放探测请求."""

    probe_count: int = 1
    """HALF_OPEN 状态允许的并发探测请求数. 默认 1 即可."""


BreakerKey = tuple[str, str]
"""(impl, api_key)"""


class CircuitBreakerStrategy(ABC):
    """熔断器策略接口.

    调用方 (LLMDispatcher) 只通过此接口判定一个 key 是否短路、记录成功/失败.
    具体状态机 (进程内 / Redis) 由实现决定.
    """

    @abstractmethod
    def is_open(self, key: BreakerKey) -> bool:
        """当前 key 是否要短路."""
        ...

    @abstractmethod
    def record_success(self, key: BreakerKey) -> None: ...

    @abstractmethod
    def record_failure(self, key: BreakerKey) -> None: ...

    @abstractmethod
    def reset(self, key: BreakerKey | None = None) -> None:
        """重置指定 key (或全部). 测试 / 运维用."""
        ...

    @abstractmethod
    def snapshot(self) -> dict[str, str]:
        """返回所有 key 的状态快照, 给 /metrics 或 admin 用."""
        ...


@dataclass
class _Record:
    """窗口内的一条失败/成功记录."""

    ts: float
    success: bool


@dataclass
class CircuitBreaker:
    """单 (impl, api_key) 的熔断器实例.

    线程安全: 所有公开方法在 _lock 保护下读写状态.
    """

    config: BreakerConfig = field(default_factory=BreakerConfig)
    _state: BreakerState = field(default=BreakerState.CLOSED, init=False)
    _opened_at: float | None = field(default=None, init=False)
    _probes_in_flight: int = field(default=0, init=False)
    _records: deque[_Record] = field(default_factory=deque, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    @property
    def state(self) -> BreakerState:
        with self._lock:
            self._maybe_transition_to_half_open_locked()
            return self._state

    def is_open(self) -> bool:
        with self._lock:
            self._maybe_transition_to_half_open_locked()
            if self._state == BreakerState.CLOSED:
                return False
            if self._state == BreakerState.OPEN:
                return True
            if self._probes_in_flight < self.config.probe_count:
                self._probes_in_flight += 1
                return False
            return True

    def record_success(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._records.append(_Record(now, True))
            self._evict_old_locked(now)
            if self._state == BreakerState.HALF_OPEN:
                self._probes_in_flight = max(0, self._probes_in_flight - 1)
                self._close_locked()

    def record_failure(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._records.append(_Record(now, False))
            self._evict_old_locked(now)
            if self._state == BreakerState.HALF_OPEN:
                self._probes_in_flight = max(0, self._probes_in_flight - 1)
                self._open_locked(now, reason="HALF_OPEN 探测失败")
                return
            if self._state == BreakerState.CLOSED:
                failures = sum(1 for r in self._records if not r.success)
                if failures >= self.config.failure_threshold:
                    self._open_locked(
                        now,
                        reason=f"窗口内失败 {failures} ≥ 阈值 {self.config.failure_threshold}",
                    )

    def _maybe_transition_to_half_open_locked(self) -> None:
        if self._state != BreakerState.OPEN or self._opened_at is None:
            return
        now = time.monotonic()
        if now - self._opened_at >= self.config.half_open_after:
            self._state = BreakerState.HALF_OPEN
            self._probes_in_flight = 0
            logger.info("熔断器 OPEN → HALF_OPEN (放行探测)")
            _notify_state_change(self, BreakerState.HALF_OPEN)

    def _open_locked(self, now: float, *, reason: str) -> None:
        prev = self._state
        self._state = BreakerState.OPEN
        self._opened_at = now
        self._probes_in_flight = 0
        logger.warning("熔断器 %s → OPEN (%s)", prev.value, reason)
        _notify_state_change(self, BreakerState.OPEN)

    def _close_locked(self) -> None:
        prev = self._state
        self._state = BreakerState.CLOSED
        self._opened_at = None
        self._probes_in_flight = 0
        self._records.clear()
        logger.info("熔断器 %s → CLOSED", prev.value)
        _notify_state_change(self, BreakerState.CLOSED)

    def _evict_old_locked(self, now: float) -> None:
        cutoff = now - self.config.window_seconds
        while self._records and self._records[0].ts < cutoff:
            self._records.popleft()

    def reset(self) -> None:
        with self._lock:
            self._state = BreakerState.CLOSED
            self._opened_at = None
            self._probes_in_flight = 0
            self._records.clear()


class InProcessCircuitBreaker(CircuitBreakerStrategy):
    """进程内熔断器策略, 默认实现.

    按 (impl, api_key) 索引 CircuitBreaker.
    实例懒创建, 配置全局统一.
    """

    def __init__(self, default_config: BreakerConfig | None = None) -> None:
        self._default_config = default_config or BreakerConfig()
        self._breakers: dict[BreakerKey, CircuitBreaker] = {}
        self._reverse: dict[int, tuple[str, str]] = {}
        """id(breaker) -> (impl, metric_model_label), 给 _notify_state_change 反查 label."""
        self._lock = threading.Lock()

    def _get(self, key: BreakerKey) -> CircuitBreaker:
        breaker = self._breakers.get(key)
        if breaker is not None:
            return breaker
        with self._lock:
            breaker = self._breakers.get(key)
            if breaker is not None:
                return breaker
            breaker = CircuitBreaker(config=self._default_config)
            self._breakers[key] = breaker
            impl, _ = key
            self._reverse[id(breaker)] = (impl, "*")
            return breaker

    def is_open(self, key: BreakerKey) -> bool:
        return self._get(key).is_open()

    def record_success(self, key: BreakerKey) -> None:
        self._get(key).record_success()

    def record_failure(self, key: BreakerKey) -> None:
        self._get(key).record_failure()

    def reset(self, key: BreakerKey | None = None) -> None:
        with self._lock:
            if key is None:
                for breaker in self._breakers.values():
                    breaker.reset()
            else:
                selected = self._breakers.get(key)
                if selected is not None:
                    selected.reset()

    def snapshot(self) -> dict[str, str]:
        with self._lock:
            out: dict[str, str] = {}
            for (impl, api_key), breaker in self._breakers.items():
                fp = f"{api_key[:6]}***" if api_key else "-"
                out[f"{impl}:{fp}"] = breaker.state.value
            return out

    def label_of(self, breaker: CircuitBreaker) -> tuple[str, str] | None:
        return self._reverse.get(id(breaker))


# ----------------------------------------------------------------------
# 兼容层: CircuitBreakerRegistry (老调用方接口)
# ----------------------------------------------------------------------
class CircuitBreakerRegistry(InProcessCircuitBreaker):
    """兼容旧 API: 老调用方使用 registry.get(impl, key, model) 拿单个 breaker.

    新代码应直接依赖 CircuitBreakerStrategy 接口, 不直接拿 CircuitBreaker 实例.
    """

    def get(self, impl: str, api_key: str, model: str) -> CircuitBreaker:
        _ = model
        return self._get((impl, api_key))

    def reset_all(self) -> None:
        super().reset(None)


# 全局单例 (兼容旧 import)
_registry = CircuitBreakerRegistry()


def get_breaker_registry() -> CircuitBreakerRegistry:
    return _registry


def get_breaker_strategy() -> CircuitBreakerStrategy:
    """新代码使用此入口拿 strategy. 默认指向全局单例."""
    return _registry


def _notify_state_change(breaker: CircuitBreaker, new_state: BreakerState) -> None:
    """状态变化回调: 推 Prometheus 指标 (软依赖, 无 prometheus_client 时 no-op)."""
    label = _registry.label_of(breaker)
    if label is None:
        return
    try:
        from forge.observability.metrics import llm_metrics

        llm_metrics.set_breaker_state(label[0], label[1], new_state.value)
    except Exception:  # noqa: BLE001
        logger.exception("breaker 状态指标上报失败 (忽略)")


BreakerOutcome = Literal["allowed", "short_circuited"]


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
