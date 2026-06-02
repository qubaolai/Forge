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

    _instances: dict[str, RedisClient] = {}
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

    # ---- Phase 6 原子操作 ----
    async def set_nx_ex(self, key: str, value: str, ttl_seconds: int) -> bool:
        """SET key value NX EX ttl. 仅在 key 不存在时设置, 返回是否成功."""
        r = await self._ensure()
        if r is None:
            return False
        try:
            return bool(await r.set(key, value, nx=True, ex=max(1, ttl_seconds)))
        except Exception as e:
            logger.warning("Redis SET NX EX 失败 key=%s err=%s", key, e)
            return False

    async def incrbyfloat(self, key: str, amount: float, ttl: int = 0) -> float | None:
        """原子累加 float 值, 返回累加后的值. ttl > 0 时设置过期."""
        r = await self._ensure()
        if r is None:
            return None
        try:
            new_val = await r.incrbyfloat(key, amount)
            if ttl > 0:
                await r.expire(key, ttl)
            return float(new_val)
        except Exception as e:
            logger.warning("Redis INCRBYFLOAT 失败 key=%s err=%s", key, e)
            return None

    async def hincrby(self, key: str, field: str, amount: int = 1, ttl: int = 0) -> int | None:
        """Hash 字段原子加 int. ttl > 0 时设置过期."""
        r = await self._ensure()
        if r is None:
            return None
        try:
            new_val = await r.hincrby(key, field, amount)
            if ttl > 0:
                await r.expire(key, ttl)
            return int(new_val)
        except Exception as e:
            logger.warning("Redis HINCRBY 失败 key=%s field=%s err=%s", key, field, e)
            return None

    async def hset_field(self, key: str, field: str, value: str, ttl: int = 0) -> bool:
        """单字段 HSET. ttl > 0 时设置过期."""
        r = await self._ensure()
        if r is None:
            return False
        try:
            await r.hset(key, field, value)
            if ttl > 0:
                await r.expire(key, ttl)
            return True
        except Exception as e:
            logger.warning("Redis HSET 字段失败 key=%s field=%s err=%s", key, field, e)
            return False

    async def hget(self, key: str, field: str) -> str | None:
        r = await self._ensure()
        if r is None:
            return None
        try:
            return await r.hget(key, field)
        except Exception:
            return None

    async def zremrangebyscore(self, key: str, min_score: float, max_score: float) -> int:
        """删除 score 在 [min, max] 之间的成员, 返回删除数."""
        r = await self._ensure()
        if r is None:
            return 0
        try:
            return int(await r.zremrangebyscore(key, min_score, max_score))
        except Exception as e:
            logger.warning("Redis ZREMRANGEBYSCORE 失败 key=%s err=%s", key, e)
            return 0

    async def zcount(self, key: str, min_score: float = float("-inf"),
                     max_score: float = float("inf")) -> int:
        """ZCOUNT, 默认返回全集合大小."""
        r = await self._ensure()
        if r is None:
            return 0
        try:
            if min_score == float("-inf") and max_score == float("inf"):
                return int(await r.zcard(key))
            return int(await r.zcount(key, min_score, max_score))
        except Exception:
            return 0

    async def pipeline_zadd_count(
        self,
        key: str,
        member: str,
        score: float,
        cutoff_score: float,
        ttl: int,
    ) -> tuple[int, bool]:
        """原子滑动窗口: 淘汰 + ZADD + ZCARD + EXPIRE. 返回 (当前计数, 是否成功)."""
        r = await self._ensure()
        if r is None:
            return 0, False
        try:
            async with r.pipeline(transaction=True) as pipe:
                pipe.zremrangebyscore(key, 0, cutoff_score)
                pipe.zadd(key, {member: score})
                pipe.zcard(key)
                pipe.expire(key, max(1, ttl))
                results = await pipe.execute()
            return int(results[2]), True
        except Exception as e:
            logger.warning("Redis 滑动窗口管道失败 key=%s err=%s", key, e)
            return 0, False

    async def zrem(self, key: str, *members: str) -> int:
        """ZREM key member [member ...]. 返回实际删除数."""
        r = await self._ensure()
        if r is None:
            return 0
        try:
            return int(await r.zrem(key, *members))
        except Exception as e:
            logger.warning("Redis ZREM 失败 key=%s err=%s", key, e)
            return 0

    async def pipeline_zadd_sum_window(
        self,
        key: str,
        member: str,
        score: float,
        cutoff_score: float,
        ttl: int,
    ) -> tuple[list[str], bool]:
        """ZSET 滑动窗口 + 返回窗口内所有 member.

        用法 (TPM 统计):
            member 形态 = "{ts}:{uuid}:{tokens}", tokens 由上层解析.

        返回 (members_in_window, ok). ok=False 表示 Redis 不可用, 上层应静默放行.
        """
        r = await self._ensure()
        if r is None:
            return [], False
        try:
            async with r.pipeline(transaction=True) as pipe:
                pipe.zremrangebyscore(key, 0, cutoff_score)
                pipe.zadd(key, {member: score})
                pipe.zrangebyscore(key, cutoff_score, "+inf")
                pipe.expire(key, max(1, ttl))
                results = await pipe.execute()
            members = results[2] or []
            return [m if isinstance(m, str) else m.decode() for m in members], True
        except Exception as e:
            logger.warning("Redis ZSET 窗口聚合失败 key=%s err=%s", key, e)
            return [], False
