"""Redis 客户端 — 连接池 + 降级策略。

Redis 不可用时自动降级为 no-op，所有操作返回 None/False。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from forge.config.settings import get_settings

logger = logging.getLogger(__name__)


class RedisClient:
    """Redis 客户端封装，支持自动降级。

    Usage:
        client = RedisClient.from_settings()
        await client.set_json("key", {"a": 1}, ttl=3600)
        data = await client.get_json("key")  # -> dict | None
    """

    _instances: dict[str, "RedisClient"] = {}
    _instances_lock = threading.Lock()

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._redis: Any = None
        self._available = False

    @classmethod
    def from_settings(cls) -> RedisClient:
        settings = get_settings()
        redis_cfg = getattr(settings, "redis", None)
        if redis_cfg is None:
            return cls._get_or_create("")
        url = getattr(redis_cfg, "url", "")
        return cls._get_or_create(url)

    @classmethod
    def _get_or_create(cls, redis_url: str) -> RedisClient:
        with cls._instances_lock:
            inst = cls._instances.get(redis_url)
            if inst is not None:
                return inst
            inst = cls(redis_url)
            cls._instances[redis_url] = inst
            return inst

    async def _ensure(self) -> Any:
        if self._redis is not None:
            return self._redis

        if not self._redis_url:
            self._available = False
            logger.warning("Redis 未配置 URL，缓存不可用")
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
        except ModuleNotFoundError as e:
            self._available = False
            self._redis = None
            logger.warning("redis-py 未安装，缓存不可用。安装: poetry add redis")
            raise e
        except Exception as e:
            self._available = False
            self._redis = None
            logger.warning("Redis 连接失败 url=%s err=%s", self._redis_url, e)
            raise e

        return self._redis

    @property
    def available(self) -> bool:
        return self._available

    async def ping(self) -> bool:
        r = await self._ensure()
        if r is None:
            return False
        try:
            await r.ping()
            self._available = True
            return True
        except Exception as e:
            self._available = False
            logger.warning("Redis PING 失败 err=%s", e)
            return False

    async def get(self, key: str) -> str | None:
        r = await self._ensure()
        if r is None:
            return None
        try:
            return await r.get(key)
        except Exception as e:
            logger.warning("Redis GET 失败 key=%s err=%s", key, e)
            return None

    async def set(self, key: str, value: str, ttl: int = 3600) -> bool:
        r = await self._ensure()
        if r is None:
            return False
        try:
            if ttl > 0:
                await r.setex(key, ttl, value)
            else:
                await r.set(key, value)
            return True
        except Exception as e:
            logger.warning("Redis SET 失败 key=%s err=%s", key, e)
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
        except Exception as e:
            logger.warning("Redis DEL 失败 keys=%s err=%s", keys, e)
            return False

    async def delete_prefix(self, prefix: str) -> int:
        """删除指定前缀的所有 key，返回删除数量。"""
        r = await self._ensure()
        if r is None:
            return 0
        pattern = f"{prefix}*"
        cursor: int | str = 0
        to_delete: list[str] = []
        try:
            while True:
                cursor, batch = await r.scan(cursor=cursor, match=pattern, count=200)
                if batch:
                    to_delete.extend(batch)
                if cursor in (0, "0"):
                    break
            if not to_delete:
                return 0
            deleted = 0
            chunk_size = 200
            for idx in range(0, len(to_delete), chunk_size):
                chunk = to_delete[idx : idx + chunk_size]
                deleted += int(await r.delete(*chunk))
            return deleted
        except Exception as e:
            logger.warning("Redis 按前缀删除失败 prefix=%s err=%s", prefix, e)
            return 0

    # ---- Hash 操作 ----
    async def hgetall(self, key: str) -> dict[str, str] | None:
        r = await self._ensure()
        if r is None:
            return None
        try:
            return await r.hgetall(key)
        except Exception as e:
            logger.warning("Redis HGETALL 失败 key=%s err=%s", key, e)
            return None

    async def hset(self, key: str, mapping: dict, ttl: int = 3600) -> bool:
        r = await self._ensure()
        if r is None:
            return False
        if not mapping:
            return True
        try:
            await r.hset(key, mapping=mapping)
            if ttl > 0:
                await r.expire(key, ttl)
            return True
        except Exception as e:
            logger.warning("Redis HSET 失败 key=%s err=%s", key, e)
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
