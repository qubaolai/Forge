"""LLM 网关入站限流: per-user 滑动窗口.

设计 (Phase 4 in-process; Phase 6 Redis 增强):
    - RPM 维度: 每分钟请求数
    - TPM 维度: 每分钟 token 数 (输入估算 token)
    - 按 user_id 分桶 (匿名用户共享一个 "" 桶)

线程安全:
    - threading.Lock 保护 dict 写
    - 时间窗口外的旧记录每次 check 时顺手 pop

降级语义:
    - rpm/tpm 任一未配置 (None) 即不限制对应维度
    - 整体未启用时 (enabled=False) 直接放行, 不消耗 lock
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


_DEFAULT_WINDOW_SECONDS: float = 60.0


class InboundRateLimitExceeded(Exception):
    """入站限流触发. 不可重试 (重试只会继续触发).

    `retry_after` 给客户端的建议等待秒数.
    """

    def __init__(self, message: str, retry_after: float = 0.0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True)
class RateCheckResult:
    allow: bool
    reason: str = ""
    retry_after: float = 0.0


@dataclass
class _UserBucket:
    """单用户的请求时间戳 + token 用量记录."""

    rpm_ts: deque[float] = field(default_factory=deque)
    tpm_records: deque[tuple[float, int]] = field(default_factory=deque)
    """(timestamp, token_count) — TPM 用."""


class InboundRateLimiter:
    """per-user 滑动窗口入站限流器.

    与 cost_tracker 的预算检查不同, 此层只看请求频率, 不关心金额.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        rpm: int | None = None,
        tpm: int | None = None,
        window_seconds: float = _DEFAULT_WINDOW_SECONDS,
    ) -> None:
        self._enabled = enabled
        self._rpm = rpm
        self._tpm = tpm
        self._window = window_seconds
        self._buckets: dict[str, _UserBucket] = defaultdict(_UserBucket)
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._enabled and (self._rpm is not None or self._tpm is not None)

    def _evict_locked(self, bucket: _UserBucket, now: float) -> None:
        cutoff = now - self._window
        while bucket.rpm_ts and bucket.rpm_ts[0] < cutoff:
            bucket.rpm_ts.popleft()
        while bucket.tpm_records and bucket.tpm_records[0][0] < cutoff:
            bucket.tpm_records.popleft()

    def check(self, user_id: str, *, estimated_tokens: int = 0) -> RateCheckResult:
        """检查 + 计入. allow=True 表示已扣减并通过; False 表示拒绝."""
        if not self.enabled:
            return RateCheckResult(allow=True)
        uid = user_id or ""
        now = time.time()
        with self._lock:
            bucket = self._buckets[uid]
            self._evict_locked(bucket, now)

            # RPM 检查
            if self._rpm is not None and len(bucket.rpm_ts) >= self._rpm:
                retry_after = max(0.0, self._window - (now - bucket.rpm_ts[0]))
                return RateCheckResult(
                    allow=False,
                    reason=f"RPM {len(bucket.rpm_ts)} ≥ {self._rpm}",
                    retry_after=retry_after,
                )
            # TPM 检查
            if self._tpm is not None and estimated_tokens > 0:
                current_tokens = sum(t for _, t in bucket.tpm_records)
                if current_tokens + estimated_tokens > self._tpm:
                    oldest_ts = bucket.tpm_records[0][0] if bucket.tpm_records else now
                    retry_after = max(0.0, self._window - (now - oldest_ts))
                    return RateCheckResult(
                        allow=False,
                        reason=f"TPM {current_tokens + estimated_tokens} > {self._tpm}",
                        retry_after=retry_after,
                    )

            # 扣减
            bucket.rpm_ts.append(now)
            if estimated_tokens > 0:
                bucket.tpm_records.append((now, estimated_tokens))
        return RateCheckResult(allow=True)

    def reset(self, user_id: str | None = None) -> None:
        """测试 / 运维: 清空某用户或全部桶."""
        with self._lock:
            if user_id is None:
                self._buckets.clear()
            else:
                self._buckets.pop(user_id or "", None)


# 全局单例 (由 lifespan / 配置初始化时注入)
_limiter: InboundRateLimiter | None = None
_init_lock = threading.Lock()


def get_inbound_rate_limiter() -> InboundRateLimiter:
    global _limiter
    if _limiter is not None:
        return _limiter
    with _init_lock:
        if _limiter is None:
            _limiter = InboundRateLimiter(enabled=False)
        return _limiter


def set_inbound_rate_limiter(limiter: InboundRateLimiter) -> None:
    """由 lifespan 在配置加载后注入实际配置的实例."""
    global _limiter
    _limiter = limiter


__all__ = [
    "InboundRateLimitExceeded",
    "InboundRateLimiter",
    "RateCheckResult",
    "get_inbound_rate_limiter",
    "set_inbound_rate_limiter",
]
