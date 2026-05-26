"""幂等去重 Pre 中间件.

防止网络超时重试导致 LLM 被调用两次. Phase 4 进程内实现, Phase 6 切 Redis NX EX.

工作流程:
    1. req.idempotency_key 为空 → 不去重, 透传
    2. 已记录该 key 的结果 → 直接返回 (短路)
    3. 已记录该 key "进行中" → 轮询等待结果 (最多 wait_seconds 秒)
    4. 未记录 → 占位"进行中", LLM 调用成功后由对应的 PostMiddleware 写结果

Phase 4 限制:
    - 进程内 dict, 多进程间不共享 (不同 worker 各算各的)
    - TTL = 30s (短窗口足以覆盖客户端重试场景, 不应作为长期缓存)
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field

from ..request import LLMRequest, LLMResponse
from .base import PostMiddleware, PreMiddleware

logger = logging.getLogger(__name__)


_TTL_SECONDS = 30.0
_POLL_INTERVAL = 0.1
_DEFAULT_WAIT = 30.0


@dataclass
class _IdemEntry:
    """单个 idempotency_key 的状态. 用 Event 等待结果."""

    event: asyncio.Event
    result: LLMResponse | None = None
    expires_at: float = 0.0


@dataclass
class IdempotencyStore:
    """进程内 idempotency store. Phase 6 由 Redis-backed 替换."""

    _entries: dict[str, _IdemEntry] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _ttl_seconds: float = _TTL_SECONDS

    def _evict_locked(self, now: float) -> None:
        expired = [k for k, e in self._entries.items() if e.expires_at <= now]
        for k in expired:
            self._entries.pop(k, None)

    def begin(self, key: str) -> tuple[bool, _IdemEntry]:
        """登记一次新调用. 返回 (是否新建, entry).

        新建 → 调用方负责执行 LLM 并 finish_success/failure;
        非新建 → 调用方应 await entry.event 等待原调用完成.
        """
        now = time.time()
        with self._lock:
            self._evict_locked(now)
            entry = self._entries.get(key)
            if entry is not None and entry.expires_at > now:
                return False, entry
            entry = _IdemEntry(
                event=asyncio.Event(),
                expires_at=now + self._ttl_seconds,
            )
            self._entries[key] = entry
            return True, entry

    def finish_success(self, key: str, resp: LLMResponse) -> None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return
            entry.result = resp
            entry.expires_at = time.time() + self._ttl_seconds
        entry.event.set()

    def finish_failure(self, key: str) -> None:
        """失败时直接删除占位, 让客户端重试可以重新登记."""
        with self._lock:
            self._entries.pop(key, None)


# 全局单例
_store: IdempotencyStore | None = None


def get_idempotency_store() -> IdempotencyStore:
    global _store
    if _store is None:
        _store = IdempotencyStore()
    return _store


def set_idempotency_store(store: IdempotencyStore) -> None:
    """由 lifespan 注入 (例如换 Redis-backed 实现)."""
    global _store
    _store = store


@dataclass
class DeduplicationMiddleware(PreMiddleware):
    """根据 idempotency_key 做幂等短路.

    若已有相同 key 的"进行中"调用, 等待其完成并返回相同结果.
    若已有完成结果, 直接短路返回.
    """

    store: IdempotencyStore | None = None
    wait_seconds: float = _DEFAULT_WAIT

    def _resolve(self) -> IdempotencyStore:
        return self.store or get_idempotency_store()

    async def process(self, req: LLMRequest) -> LLMResponse | None:
        if not req.idempotency_key:
            return None
        store = self._resolve()
        is_new, entry = store.begin(req.idempotency_key)
        if is_new:
            # 首次调用: 让流程继续 (LLMGateway 调 dispatcher), 由 DedupCompleteMiddleware
            # 在 Post 阶段 finish_success
            return None

        # 命中已有 entry: 已完成 → 直接返回, 未完成 → 等待
        if entry.result is not None:
            logger.debug("幂等命中 (已完成): key=%s", req.idempotency_key[:12] + "***")
            return entry.result

        logger.debug(
            "幂等命中 (等待中): key=%s, 等待原调用完成", req.idempotency_key[:12] + "***"
        )
        try:
            await asyncio.wait_for(entry.event.wait(), timeout=self.wait_seconds)
        except asyncio.TimeoutError:
            logger.warning(
                "幂等等待超时 (%.1fs): key=%s, 改走新调用",
                self.wait_seconds,
                req.idempotency_key[:12] + "***",
            )
            return None
        return entry.result


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
            # 缓存命中本身就是短路, 不重复登记到 dedup
            return resp
        self._resolve().finish_success(req.idempotency_key, resp)
        return resp


__all__ = [
    "DedupCompleteMiddleware",
    "DeduplicationMiddleware",
    "IdempotencyStore",
    "get_idempotency_store",
    "set_idempotency_store",
]
