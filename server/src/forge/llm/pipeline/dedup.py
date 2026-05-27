"""幂等去重 Pre 中间件.

防止网络超时重试导致 LLM 被调用两次.

工作流程:
    1. req.idempotency_key 为空 → 不去重, 透传
    2. 已记录该 key 的结果 → 直接返回 (短路)
    3. 已记录该 key "进行中" → 等待原调用完成 (最多 wait_seconds)
    4. 未记录 → 占位"进行中", LLM 调用成功后由 PostMiddleware 写结果

实现:
    - InProcessIdempotencyStore: 进程内 dict + asyncio.Event 等待
    - RedisIdempotencyStore: SET NX EX 占位 + 结果存 JSON + 轮询等待
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..request import LLMRequest, LLMResponse
from .base import PostMiddleware, PreMiddleware

logger = logging.getLogger(__name__)


_TTL_SECONDS = 30.0
_POLL_INTERVAL = 0.1
_DEFAULT_WAIT = 30.0


@dataclass
class IdempotencyOutcome:
    """begin() 的返回值.

    is_new=True : 首次登记, 调用方负责执行 LLM
    is_new=False: 已存在登记, result/event 用于等待原调用完成
    """

    is_new: bool
    result: LLMResponse | None = None
    """已完成时为结果, 进行中为 None."""

    waiter: asyncio.Event | None = None
    """In-process 实现的等待用. Redis 实现为 None (调用方走轮询)."""


@runtime_checkable
class IdempotencyStore(Protocol):
    """幂等存储策略接口."""

    async def begin(self, key: str) -> IdempotencyOutcome: ...

    async def finish_success(self, key: str, resp: LLMResponse) -> None: ...

    async def finish_failure(self, key: str) -> None: ...

    async def wait_for(self, key: str, *, timeout: float) -> LLMResponse | None:
        """等待原调用完成. 超时返回 None."""
        ...


@dataclass
class _IdemEntry:
    event: asyncio.Event
    result: LLMResponse | None = None
    expires_at: float = 0.0


@dataclass
class InProcessIdempotencyStore:
    """进程内 idempotency store."""

    _entries: dict[str, _IdemEntry] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _ttl_seconds: float = _TTL_SECONDS

    def _evict_locked(self, now: float) -> None:
        expired = [k for k, e in self._entries.items() if e.expires_at <= now]
        for k in expired:
            self._entries.pop(k, None)

    async def begin(self, key: str) -> IdempotencyOutcome:
        now = time.time()
        with self._lock:
            self._evict_locked(now)
            entry = self._entries.get(key)
            if entry is not None and entry.expires_at > now:
                return IdempotencyOutcome(
                    is_new=False, result=entry.result, waiter=entry.event
                )
            entry = _IdemEntry(
                event=asyncio.Event(),
                expires_at=now + self._ttl_seconds,
            )
            self._entries[key] = entry
            return IdempotencyOutcome(is_new=True, waiter=entry.event)

    async def finish_success(self, key: str, resp: LLMResponse) -> None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return
            entry.result = resp
            entry.expires_at = time.time() + self._ttl_seconds
            event = entry.event
        event.set()

    async def finish_failure(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    async def wait_for(self, key: str, *, timeout: float) -> LLMResponse | None:
        with self._lock:
            entry = self._entries.get(key)
        if entry is None:
            return None
        try:
            await asyncio.wait_for(entry.event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        return entry.result


@dataclass
class RedisIdempotencyStore:
    """Redis-backed idempotency store. 多实例共享.

    Key 形态:
        forge:llm:idem:{key}  ── string, 内容 = JSON dump of LLMResponse 或 "PENDING"

    begin():
        SET key "PENDING" NX EX ttl  → 成功表示首次 (is_new=True)
        失败 → 已有, 读取当前值: "PENDING" 表示进行中, JSON 表示已完成

    finish_success(): SET key {json} EX ttl (覆盖 PENDING)
    finish_failure(): DEL key (让客户端重试可重新登记)
    wait_for(): 轮询 GET, 直到拿到 JSON 或超时
    """

    redis_client: object
    """RedisClient 实例 (鸭子类型)."""

    ttl_seconds: int = int(_TTL_SECONDS)
    poll_interval: float = _POLL_INTERVAL

    @staticmethod
    def _redis_key(key: str) -> str:
        return f"forge:llm:idem:{key}"

    @staticmethod
    def _serialize(resp: LLMResponse) -> str:
        # 仅序列化关键字段 (raw 可能有 SDK 对象, 不可序列化)
        return json.dumps(
            {
                "content": resp.content,
                "model": resp.model,
                "provider": resp.provider,
                "usage": resp.usage,
                "finish_reason": resp.finish_reason,
                "tool_calls": resp.tool_calls,
                "cache_hit": resp.cache_hit,
                "cache_type": resp.cache_type,
                "cost_usd": resp.cost_usd,
                "latency_ms": resp.latency_ms,
                "fallback_position": resp.fallback_position,
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _deserialize(raw: str) -> LLMResponse | None:
        if raw == "PENDING":
            return None
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        return LLMResponse(**data)

    async def begin(self, key: str) -> IdempotencyOutcome:
        rk = self._redis_key(key)
        try:
            ok = await self.redis_client.set_nx_ex(rk, "PENDING", self.ttl_seconds)
        except Exception:  # noqa: BLE001
            logger.exception("Redis 幂等 begin 失败, 降级允许 LLM 调用")
            return IdempotencyOutcome(is_new=True)
        if ok:
            return IdempotencyOutcome(is_new=True)
        # 已有: 看当前是否完成
        raw = await self.redis_client.get(rk)
        if raw is None:
            # 刚过期/被删, 当作新调用
            return IdempotencyOutcome(is_new=True)
        result = self._deserialize(raw)
        return IdempotencyOutcome(is_new=False, result=result)

    async def finish_success(self, key: str, resp: LLMResponse) -> None:
        rk = self._redis_key(key)
        try:
            await self.redis_client.set(rk, self._serialize(resp), ttl=self.ttl_seconds)
        except Exception:  # noqa: BLE001
            logger.exception("Redis 幂等 finish_success 失败")

    async def finish_failure(self, key: str) -> None:
        try:
            await self.redis_client.delete(self._redis_key(key))
        except Exception:  # noqa: BLE001
            logger.exception("Redis 幂等 finish_failure 失败")

    async def wait_for(self, key: str, *, timeout: float) -> LLMResponse | None:
        rk = self._redis_key(key)
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = await self.redis_client.get(rk)
            if raw is None:
                return None
            result = self._deserialize(raw)
            if result is not None:
                return result
            await asyncio.sleep(self.poll_interval)
        return None


# 全局单例
_store: IdempotencyStore | None = None


def get_idempotency_store() -> IdempotencyStore:
    global _store
    if _store is None:
        _store = InProcessIdempotencyStore()
    return _store


def set_idempotency_store(store: IdempotencyStore) -> None:
    """由 lifespan 注入 (例如换 Redis-backed 实现)."""
    global _store
    _store = store


@dataclass
class DeduplicationMiddleware(PreMiddleware):
    """根据 idempotency_key 做幂等短路."""

    store: IdempotencyStore | None = None
    wait_seconds: float = _DEFAULT_WAIT

    def _resolve(self) -> IdempotencyStore:
        return self.store or get_idempotency_store()

    async def process(self, req: LLMRequest) -> LLMResponse | None:
        if not req.idempotency_key:
            return None
        store = self._resolve()
        outcome = await store.begin(req.idempotency_key)
        if outcome.is_new:
            # 首次调用: 让流程继续, 由 DedupCompleteMiddleware 在 Post 写结果
            return None

        # 已有: 完成 → 直接返回; 未完成 → 等待
        if outcome.result is not None:
            logger.debug("幂等命中 (已完成): key=%s***", req.idempotency_key[:12])
            return outcome.result

        logger.debug("幂等命中 (等待中): key=%s***, 等待原调用完成", req.idempotency_key[:12])
        result = await store.wait_for(req.idempotency_key, timeout=self.wait_seconds)
        if result is None:
            logger.warning(
                "幂等等待超时 (%.1fs): key=%s***, 改走新调用",
                self.wait_seconds,
                req.idempotency_key[:12],
            )
            return None
        return result


@dataclass
class DedupCompleteMiddleware(PostMiddleware):
    """Post: 把成功响应写回 idempotency store, 唤醒等待者."""

    store: IdempotencyStore | None = None

    def _resolve(self) -> IdempotencyStore:
        return self.store or get_idempotency_store()

    async def process(self, req: LLMRequest, resp: LLMResponse) -> LLMResponse:
        if not req.idempotency_key:
            return resp
        if resp.cache_hit:
            return resp
        await self._resolve().finish_success(req.idempotency_key, resp)
        return resp


__all__ = [
    "DedupCompleteMiddleware",
    "DeduplicationMiddleware",
    "IdempotencyOutcome",
    "IdempotencyStore",
    "InProcessIdempotencyStore",
    "RedisIdempotencyStore",
    "get_idempotency_store",
    "set_idempotency_store",
]
