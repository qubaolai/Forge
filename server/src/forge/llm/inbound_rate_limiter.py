"""LLM 网关入站限流: per-user 滑动窗口.

设计:
    - 进程内默认 (InProcessInboundRateLimiter): 单机部署, 零额外依赖
    - Redis 增强 (RedisInboundRateLimiter, Phase 6): 多实例共享限流状态, ZADD/ZCOUNT
    - 调用方通过 InboundRateLimiter Protocol 调用, 实现可热切换

降级语义:
    - rpm/tpm 任一未配置 (None) 即不限制对应维度
    - 整体未启用时 (enabled=False) 直接放行
    - Redis 实现失败时静默降级 (允许通过, 打日志), 避免 Redis 故障导致服务雪崩
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

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


@runtime_checkable
class InboundRateLimiter(Protocol):
    """限流器策略接口."""

    @property
    def enabled(self) -> bool: ...

    async def check(self, user_id: str, *, estimated_tokens: int = 0) -> RateCheckResult: ...

    async def reset(self, user_id: str | None = None) -> None: ...


@dataclass
class _UserBucket:
    """单用户的请求时间戳 + token 用量记录."""

    rpm_ts: deque[float] = field(default_factory=deque)
    tpm_records: deque[tuple[float, int]] = field(default_factory=deque)
    """(timestamp, token_count) — TPM 用."""


class InProcessInboundRateLimiter:
    """per-user 滑动窗口入站限流器 (进程内). 默认实现."""

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

    async def check(self, user_id: str, *, estimated_tokens: int = 0) -> RateCheckResult:
        if not self.enabled:
            return RateCheckResult(allow=True)
        uid = user_id or ""
        now = time.time()
        with self._lock:
            bucket = self._buckets[uid]
            self._evict_locked(bucket, now)

            if self._rpm is not None and len(bucket.rpm_ts) >= self._rpm:
                retry_after = max(0.0, self._window - (now - bucket.rpm_ts[0]))
                return RateCheckResult(
                    allow=False,
                    reason=f"RPM {len(bucket.rpm_ts)} ≥ {self._rpm}",
                    retry_after=retry_after,
                )
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

            bucket.rpm_ts.append(now)
            if estimated_tokens > 0:
                bucket.tpm_records.append((now, estimated_tokens))
        return RateCheckResult(allow=True)

    async def reset(self, user_id: str | None = None) -> None:
        with self._lock:
            if user_id is None:
                self._buckets.clear()
            else:
                self._buckets.pop(user_id or "", None)


class RedisInboundRateLimiter:
    """Redis ZSET 滑动窗口限流器 (Phase 6).

    Key 形态:
        forge:llm:ratelimit:rpm:{user_id}  ── ZSET, score=ts, member="{ts}:{uuid}"
        forge:llm:ratelimit:tpm:{user_id}  ── ZSET, score=ts, member="{ts}:{uuid}:{tokens}"
        TPM 的 token 数编码进 member, ZRANGE 后解析求和 (真正的滑动窗口).

    RPM 流程:
        1. pipeline: ZREMRANGEBYSCORE(旧) + ZADD(新) + ZCARD + EXPIRE
        2. 若 ZCARD > rpm → ZREM 自己刚加入的成员 (回滚), 返回拒绝
           → 计数严格不超 rpm, 不再有"+1 偏差"

    TPM 流程:
        1. pipeline_zadd_sum_window: 淘汰旧 + ZADD 新 + ZRANGE 窗口内全部 member
        2. 解析每个 member 末段的 tokens, 求和
        3. 若 sum > tpm → ZREM 自己的成员回滚, 返回拒绝

    降级: Redis 不可用 → 静默允许通过, 不阻断业务.
    """

    _RPM_KEY = "forge:llm:ratelimit:rpm:{uid}"
    _TPM_KEY = "forge:llm:ratelimit:tpm:{uid}"

    def __init__(
        self,
        redis_client,
        *,
        enabled: bool = True,
        rpm: int | None = None,
        tpm: int | None = None,
        window_seconds: float = _DEFAULT_WINDOW_SECONDS,
    ) -> None:
        self._redis = redis_client
        self._enabled = enabled
        self._rpm = rpm
        self._tpm = tpm
        self._window = window_seconds

    @property
    def enabled(self) -> bool:
        return self._enabled and (self._rpm is not None or self._tpm is not None)

    async def check(self, user_id: str, *, estimated_tokens: int = 0) -> RateCheckResult:
        if not self.enabled:
            return RateCheckResult(allow=True)
        uid = user_id or "_anon"
        now = time.time()
        cutoff = now - self._window
        ttl = int(self._window) + 5

        # ---- RPM ----
        if self._rpm is not None:
            key = self._RPM_KEY.format(uid=uid)
            member = f"{now:.6f}:{uuid.uuid4().hex}"
            count, ok = await self._redis.pipeline_zadd_count(
                key, member=member, score=now, cutoff_score=cutoff, ttl=ttl,
            )
            if not ok:
                logger.debug("Redis 限流降级 (RPM): 允许通过")
                return RateCheckResult(allow=True)
            if count > self._rpm:
                # 回滚自己刚加入的成员, 保持计数严格不超 rpm
                try:
                    await self._redis.zrem(key, member)
                except Exception:  # noqa: BLE001
                    logger.debug("RPM 拒绝后回滚 ZREM 失败 (已忽略)", exc_info=True)
                return RateCheckResult(
                    allow=False,
                    reason=f"RPM {count - 1} ≥ {self._rpm}",
                    retry_after=self._window,
                )

        # ---- TPM (ZSET 滑动窗口) ----
        if self._tpm is not None and estimated_tokens > 0:
            key = self._TPM_KEY.format(uid=uid)
            member = f"{now:.6f}:{uuid.uuid4().hex}:{estimated_tokens}"
            members, ok = await self._redis.pipeline_zadd_sum_window(
                key, member=member, score=now, cutoff_score=cutoff, ttl=ttl,
            )
            if not ok:
                logger.debug("Redis 限流降级 (TPM): 允许通过")
                return RateCheckResult(allow=True)
            total = 0
            for m in members:
                try:
                    total += int(m.rsplit(":", 1)[-1])
                except (ValueError, IndexError):
                    continue
            if total > self._tpm:
                try:
                    await self._redis.zrem(key, member)
                except Exception:  # noqa: BLE001
                    logger.debug("TPM 拒绝后回滚 ZREM 失败 (已忽略)", exc_info=True)
                return RateCheckResult(
                    allow=False,
                    reason=f"TPM {total} > {self._tpm}",
                    retry_after=self._window,
                )

        return RateCheckResult(allow=True)

    async def reset(self, user_id: str | None = None) -> None:
        """实际删除 Redis key. user_id=None 时仅删除调用方传入的 anon 桶."""
        if user_id is None:
            # Redis 端不支持安全的"按前缀全部删除" (KEYS/SCAN 大 key 风险), 这里仅清空 anon
            uid = "_anon"
        else:
            uid = user_id or "_anon"
        keys = [self._RPM_KEY.format(uid=uid), self._TPM_KEY.format(uid=uid)]
        try:
            await self._redis.delete(*keys)
        except Exception:  # noqa: BLE001
            logger.debug("RedisInboundRateLimiter.reset 删除失败 (已忽略)", exc_info=True)


# 全局单例 (由 lifespan / 配置初始化时注入)
_limiter: InboundRateLimiter | None = None
_init_lock = threading.Lock()


def get_inbound_rate_limiter() -> InboundRateLimiter:
    global _limiter
    if _limiter is not None:
        return _limiter
    with _init_lock:
        if _limiter is None:
            _limiter = InProcessInboundRateLimiter(enabled=False)
        return _limiter


def set_inbound_rate_limiter(limiter: InboundRateLimiter) -> None:
    """由 lifespan 在配置加载后注入实际配置的实例."""
    global _limiter
    _limiter = limiter


__all__ = [
    "InboundRateLimitExceeded",
    "InboundRateLimiter",
    "InProcessInboundRateLimiter",
    "RateCheckResult",
    "RedisInboundRateLimiter",
    "get_inbound_rate_limiter",
    "set_inbound_rate_limiter",
]
