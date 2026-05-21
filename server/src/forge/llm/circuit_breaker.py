"""LLM 调用熔断器: provider 级时间窗口熔断, 消除"宕机 provider 每次等满 timeout"问题.

为什么需要熔断:
    retry.py 只做"请求内重试"(同一个 provider 内多次尝试).
    没有熔断时, primary provider 完全宕机 → 每个请求都要等 timeout (30s) 后才能 fallback,
    首包延迟雪崩到 30s+. 熔断器记忆"这个 provider 最近大量失败", 直接跳过, 0 等待切下一个.

状态机:
    CLOSED          正常通过, 累计失败
    │
    │  窗口内失败 ≥ failure_threshold
    ▼
    OPEN            直接拒绝 (is_open()=True), 让 FallbackChain 跳到下一个 entry
    │
    │  half_open_after 秒后
    ▼
    HALF_OPEN       放行 probe_count 次探测请求
    │
    │  探测成功 → CLOSED
    │  探测失败 → OPEN (重新等 half_open_after)
    ▼

Key 粒度: (impl, api_key, model)
    - 同 (impl, model) 但不同 api_key 视为不同熔断单元: 多 key 部署时
      一个坏 key 不会让所有 key 都被熔断
    - 同 impl 同 key 但不同 model 也是不同单元: model 个体故障互不影响

不持久化:
    进程内状态, 重启重置. 宕机 provider 重启服务后 breaker 从 CLOSED 开始重新学习.
"""

from __future__ import annotations

import logging
import threading
import time
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
    """HALF_OPEN 状态允许的并发探测请求数. 默认 1 即可: 失败立刻回 OPEN,
    成功 1 次就回 CLOSED."""


@dataclass
class _Record:
    """窗口内的一条失败/成功记录."""

    ts: float
    success: bool


@dataclass
class CircuitBreaker:
    """单 (impl, api_key, model) 的熔断器实例.

    线程安全: 所有公开方法在 _lock 保护下读写状态.
    """

    config: BreakerConfig = field(default_factory=BreakerConfig)
    _state: BreakerState = field(default=BreakerState.CLOSED, init=False)
    _opened_at: float | None = field(default=None, init=False)
    """进入 OPEN 状态的时间戳, 用于判断 half_open_after."""
    _probes_in_flight: int = field(default=0, init=False)
    """HALF_OPEN 状态下已放行的探测请求数."""
    _records: deque[_Record] = field(default_factory=deque, init=False)
    """滑动窗口记录."""
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    # ------------------------------------------------------------------
    # 状态查询 + 状态推进
    # ------------------------------------------------------------------
    @property
    def state(self) -> BreakerState:
        with self._lock:
            self._maybe_transition_to_half_open_locked()
            return self._state

    def is_open(self) -> bool:
        """判断当前是否需要短路.

        副作用: 自然过渡 OPEN -> HALF_OPEN (基于时间). HALF_OPEN 下还放行
        probe_count 个请求, 之后又 is_open()=True.
        """
        with self._lock:
            self._maybe_transition_to_half_open_locked()
            if self._state == BreakerState.CLOSED:
                return False
            if self._state == BreakerState.OPEN:
                return True
            # HALF_OPEN: 放行有限个探测
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
                # 探测成功 → 闭合
                self._probes_in_flight = max(0, self._probes_in_flight - 1)
                self._close_locked()
            # CLOSED 时无须特别处理

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

    # ------------------------------------------------------------------
    # 内部状态转换 (调用方持锁)
    # ------------------------------------------------------------------
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
        # 重置统计, 避免上次失败计入本轮
        self._records.clear()
        logger.info("熔断器 %s → CLOSED", prev.value)
        _notify_state_change(self, BreakerState.CLOSED)

    def _evict_old_locked(self, now: float) -> None:
        """丢弃窗口外的记录."""
        cutoff = now - self.config.window_seconds
        while self._records and self._records[0].ts < cutoff:
            self._records.popleft()

    # 测试 / 运维口子
    def reset(self) -> None:
        with self._lock:
            self._state = BreakerState.CLOSED
            self._opened_at = None
            self._probes_in_flight = 0
            self._records.clear()


# ----------------------------------------------------------------------
# Registry — 进程级熔断器实例池
# ----------------------------------------------------------------------
BreakerKey = tuple[str, str, str]  # (impl, api_key, model)


class CircuitBreakerRegistry:
    """按 (impl, api_key, model) 索引 CircuitBreaker.

    实例懒创建, 配置全局统一 (本期不做 per-key 配置, 后续按需要扩).
    Registry 同时维护"breaker -> (impl, model)"反向索引,
    给 Prometheus 状态指标用 (state change 回调要知道 provider/model 标签).
    """

    def __init__(self, default_config: BreakerConfig | None = None) -> None:
        self._default_config = default_config or BreakerConfig()
        self._breakers: dict[BreakerKey, CircuitBreaker] = {}
        self._reverse: dict[int, tuple[str, str]] = {}
        """id(breaker) -> (impl, model), 给 _notify_state_change 反查 label."""
        self._lock = threading.Lock()

    def get(self, impl: str, api_key: str, model: str) -> CircuitBreaker:
        key: BreakerKey = (impl, api_key, model)
        breaker = self._breakers.get(key)
        if breaker is not None:
            return breaker
        with self._lock:
            breaker = self._breakers.get(key)
            if breaker is not None:
                return breaker
            breaker = CircuitBreaker(config=self._default_config)
            self._breakers[key] = breaker
            self._reverse[id(breaker)] = (impl, model)
            return breaker

    def label_of(self, breaker: CircuitBreaker) -> tuple[str, str] | None:
        return self._reverse.get(id(breaker))

    def snapshot(self) -> dict[str, str]:
        """返回所有 breaker 的状态快照, 给 /metrics 或 admin 用.

        key 是 "impl:fingerprint:model" 格式 (api_key 脱敏).
        """
        with self._lock:
            out: dict[str, str] = {}
            for (impl, api_key, model), breaker in self._breakers.items():
                fp = f"{api_key[:6]}***" if api_key else "-"
                out[f"{impl}:{fp}:{model}"] = breaker.state.value
            return out

    def reset_all(self) -> None:
        with self._lock:
            for breaker in self._breakers.values():
                breaker.reset()


# 全局单例
_registry = CircuitBreakerRegistry()


def get_breaker_registry() -> CircuitBreakerRegistry:
    return _registry


def _notify_state_change(breaker: CircuitBreaker, new_state: BreakerState) -> None:
    """状态变化回调: 推 Prometheus 指标 (软依赖, 无 prometheus_client 时 no-op)."""
    label = _registry.label_of(breaker)
    if label is None:
        # 未注册到 registry 的独立 breaker (例如测试场景), 不打指标
        return
    try:
        from forge.observability.metrics import llm_metrics

        llm_metrics.set_breaker_state(label[0], label[1], new_state.value)
    except Exception:  # noqa: BLE001
        logger.exception("breaker 状态指标上报失败 (忽略)")


__all__ = [
    "BreakerConfig",
    "BreakerState",
    "CircuitBreaker",
    "CircuitBreakerRegistry",
    "get_breaker_registry",
]


# 类型导出辅助
BreakerOutcome = Literal["allowed", "short_circuited"]
