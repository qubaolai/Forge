"""精确匹配缓存.

Phase 4: 进程内 LRU (容量 256, 默认).
Phase 6: Redis backend (多实例共享, 由 lifespan 注入).

缓存条件:
    - req.cache_enabled == True
    - req.temperature == 0 (确定性输出, 缓存有意义)
    - 仅非流式 (complete / complete_with_tools)
    - 工具调用 (req.tools 非空) 默认不缓存

Key 生成:
    sha256(provider:model:max_tokens:canonical_json(messages))
    → "forge:llm:exact:{digest}"

    注意: max_tokens 参与 key 计算, 防止两次仅 max_tokens 不同的请求拿到截断的响应.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256

from ..providers.base import ChatMessage
from ..request import LLMResponse

logger = logging.getLogger(__name__)


_KEY_PREFIX = "forge:llm:exact:"


def make_cache_key(
    provider: str,
    model: str,
    messages: Iterable[ChatMessage],
    *,
    max_tokens: int | None = None,
) -> str:
    """计算缓存 key.

    参与 hash 的字段:
        - provider / model: 不同模型输出不同 → 不能共用 cache
        - max_tokens: 截断长度不同 → 不能共用 cache (老实现遗漏, 会拿到截断响应)
        - messages 的 role + content: 忽略 raw 等附加字段
    """
    canonical = json.dumps(
        [{"role": m.role, "content": m.content or ""} for m in messages],
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    mt_part = str(max_tokens) if max_tokens is not None else "-"
    digest = sha256(f"{provider}:{model}:{mt_part}:{canonical}".encode()).hexdigest()
    return f"{_KEY_PREFIX}{digest}"


class ExactCacheBackend(ABC):
    """缓存后端抽象, Phase 6 切 Redis 时只换实现."""

    @abstractmethod
    async def get(self, key: str) -> LLMResponse | None: ...

    @abstractmethod
    async def set(self, key: str, resp: LLMResponse, ttl_seconds: int = 3600) -> None: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...


@dataclass
class _CacheEntry:
    response: LLMResponse
    expires_at: float


class InProcessLRUCache(ExactCacheBackend):
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


class RedisExactCache(ExactCacheBackend):
    """Redis-backed 精确缓存. Phase 6 多实例共享.

    Key 形态:
        forge:llm:exact:{sha256_digest}  ── string, 值 = JSON of LLMResponse 关键字段

    Redis 不可用时由 RedisClient 自动降级 (get/set 返回 None/False);
    上层 ExactCacheMiddleware 会忽略错误, 不影响业务.
    """

    def __init__(self, redis_client) -> None:
        self._redis = redis_client

    @staticmethod
    def _serialize(resp: LLMResponse) -> str:
        import json
        return json.dumps(
            {
                "content": resp.content,
                "model": resp.model,
                "provider": resp.provider,
                "usage": resp.usage,
                "finish_reason": resp.finish_reason,
                "tool_calls": resp.tool_calls,
                "cache_hit": False,  # 命中时由 middleware 重置为 True
                "cache_type": None,
                "cost_usd": resp.cost_usd,
                "latency_ms": resp.latency_ms,
                "fallback_position": resp.fallback_position,
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _deserialize(raw: str) -> LLMResponse | None:
        import json
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        return LLMResponse(**data)

    async def get(self, key: str) -> LLMResponse | None:
        try:
            raw = await self._redis.get(key)
        except Exception:  # noqa: BLE001
            logger.debug("Redis 精确缓存读取失败 (降级)", exc_info=True)
            return None
        if raw is None:
            return None
        return self._deserialize(raw)

    async def set(self, key: str, resp: LLMResponse, ttl_seconds: int = 3600) -> None:
        try:
            await self._redis.set(key, self._serialize(resp), ttl=ttl_seconds)
        except Exception:  # noqa: BLE001
            logger.debug("Redis 精确缓存写入失败 (降级)", exc_info=True)

    async def delete(self, key: str) -> None:
        with suppress(Exception):
            await self._redis.delete(key)


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
    "RedisExactCache",
    "get_exact_cache",
    "make_cache_key",
    "set_exact_cache",
]
