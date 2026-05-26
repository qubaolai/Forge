"""精确匹配缓存.

Phase 4: 进程内 LRU (容量 256), Redis backend 留 Phase 6.

缓存条件:
    - req.cache_enabled == True
    - req.temperature == 0 (确定性输出, 缓存有意义)
    - 仅非流式 (complete / complete_with_tools)
    - 工具调用 (req.tools 非空) 默认不缓存 (Phase 4 跳过, 后续可放开)

Key 生成:
    sha256(provider:model:canonical_json(messages))
    → "forge:llm:exact:{digest}"
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol

from ..providers.base import ChatMessage
from ..request import LLMResponse

logger = logging.getLogger(__name__)


_KEY_PREFIX = "forge:llm:exact:"


def make_cache_key(provider: str, model: str, messages: Iterable[ChatMessage]) -> str:
    """计算缓存 key. 仅依赖 (provider, model, role+content), 忽略 raw 字段."""
    canonical = json.dumps(
        [{"role": m.role, "content": m.content or ""} for m in messages],
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = sha256(f"{provider}:{model}:{canonical}".encode()).hexdigest()
    return f"{_KEY_PREFIX}{digest}"


class ExactCacheBackend(Protocol):
    """缓存后端抽象, Phase 6 切 Redis 时只换实现."""

    async def get(self, key: str) -> LLMResponse | None: ...

    async def set(self, key: str, resp: LLMResponse, ttl_seconds: int = 3600) -> None: ...

    async def delete(self, key: str) -> None: ...


@dataclass
class _CacheEntry:
    response: LLMResponse
    expires_at: float


class InProcessLRUCache:
    """进程内 LRU + TTL 缓存. 适合单机或开发场景."""

    def __init__(self, max_size: int = 256) -> None:
        self._store: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._lock = threading.Lock()
        self._max = max_size

    async def get(self, key: str) -> LLMResponse | None:
        now = time.time()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if entry.expires_at <= now:
                self._store.pop(key, None)
                return None
            self._store.move_to_end(key)
        return entry.response

    async def set(self, key: str, resp: LLMResponse, ttl_seconds: int = 3600) -> None:
        expires_at = time.time() + max(1, ttl_seconds)
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = _CacheEntry(response=resp, expires_at=expires_at)
            while len(self._store) > self._max:
                self._store.popitem(last=False)

    async def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def size(self) -> int:
        return len(self._store)


# 全局单例
_cache_backend: ExactCacheBackend | None = None
_init_lock = threading.Lock()


def get_exact_cache() -> ExactCacheBackend:
    global _cache_backend
    if _cache_backend is not None:
        return _cache_backend
    with _init_lock:
        if _cache_backend is None:
            _cache_backend = InProcessLRUCache()
        return _cache_backend


def set_exact_cache(backend: ExactCacheBackend) -> None:
    """由 lifespan 在 Redis 可用时注入 RedisExactCache (Phase 6)."""
    global _cache_backend
    _cache_backend = backend


__all__ = [
    "ExactCacheBackend",
    "InProcessLRUCache",
    "get_exact_cache",
    "make_cache_key",
    "set_exact_cache",
]
