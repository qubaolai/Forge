"""Redis 客户端 — 连接池 + 降级策略。

Redis 不可用时自动降级为 no-op，所有操作返回 None/False。
"""

from __future__ import annotations

import logging
from typing import Any

from config.settings import get_settings

logger = logging.getLogger(__name__)


class RedisClient:
    """Redis 客户端封装，支持自动降级。

    Usage:
        client = RedisClient.from_settings()
        await client.set_json("key", {"a": 1}, ttl=3600)
        data = await client.get_json("key")  # -> dict | None
    """

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._redis: Any = None
        self._available = False

    @classmethod
    def from_settings(cls) -> RedisClient:
        settings = get_settings()
        redis_cfg = getattr(settings, "redis", None)
        if redis_cfg is None:
            return cls("")
        url = getattr(redis_cfg, "url", "")
        return cls(url)

    async def _ensure(self) -> Any:
        if self._redis is not None:
            return self._redis

        if not self._redis_url:
            self._available = False
            return None

        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(
                self._redis_url,
                socket_connect_timeout=2,
                socket_timeout=2,
                decode_responses=True,
                max_connections=10,
            )
            await self._redis.ping()
            self._available = True
            logger.info("Redis 连接就绪: %s", self._redis_url)
        except Exception:
            self._available = False
            self._redis = None
            logger.debug("Redis 不可用，降级直连 MySQL: %s", self._redis_url)

        return self._redis

    @property
    def available(self) -> bool:
        return self._available

    async def get(self, key: str) -> str | None:
        r = await self._ensure()
        if r is None:
            return None
        try:
            return await r.get(key)
        except Exception:
            logger.debug("Redis GET 失败 key=%s", key, exc_info=True)
            return None

    async def set(self, key: str, value: str, ttl: int = 3600) -> bool:
        r = await self._ensure()
        if r is None:
            return False
        try:
            await r.setex(key, ttl, value)
            return True
        except Exception:
            logger.debug("Redis SET 失败 key=%s", key, exc_info=True)
            return False

    async def get_json(self, key: str) -> dict | list | None:
        import json

        raw = await self.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    async def set_json(self, key: str, value: Any, ttl: int = 3600) -> bool:
        import json

        return await self.set(key, json.dumps(value, ensure_ascii=False), ttl)

    async def delete(self, *keys: str) -> bool:
        r = await self._ensure()
        if r is None:
            return False
        try:
            await r.delete(*keys)
            return True
        except Exception:
            logger.debug("Redis DEL 失败 keys=%s", keys, exc_info=True)
            return False

    # ---- Hash 操作 ----
    async def hgetall(self, key: str) -> dict[str, str] | None:
        r = await self._ensure()
        if r is None:
            return None
        try:
            return await r.hgetall(key)
        except Exception:
            return None

    async def hset(self, key: str, mapping: dict, ttl: int = 3600) -> bool:
        r = await self._ensure()
        if r is None:
            return False
        try:
            await r.hset(key, mapping=mapping)
            await r.expire(key, ttl)
            return True
        except Exception:
            return False

    # ---- List 操作 ----
    async def lrange(self, key: str, start: int, end: int) -> list[str] | None:
        r = await self._ensure()
        if r is None:
            return None
        try:
            return await r.lrange(key, start, end)
        except Exception:
            return None

    async def rpush(self, key: str, *values: str, ttl: int = 1800) -> bool:
        r = await self._ensure()
        if r is None:
            return False
        try:
            await r.rpush(key, *values)
            await r.expire(key, ttl)
            return True
        except Exception:
            return False

    # ---- ZSet 操作 ----
    async def zadd(self, key: str, mapping: dict[str, float], ttl: int = 3600) -> bool:
        r = await self._ensure()
        if r is None:
            return False
        try:
            await r.zadd(key, mapping)
            await r.expire(key, ttl)
            return True
        except Exception:
            return False

    async def zrevrange(
        self, key: str, start: int, end: int
    ) -> list[str] | None:
        r = await self._ensure()
        if r is None:
            return None
        try:
            return await r.zrevrange(key, start, end)
        except Exception:
            return None
