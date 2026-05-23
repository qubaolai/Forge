"""Session 缓存层 — Redis Hash + ZSet。

Key 结构:
    forge:session:{session_business_id}   → Hash   TTL 3600s
    forge:user_sessions:{user_business_id} → ZSet   TTL 3600s

缓存策略: cache-aside。读未命中返回 None，调用方查 DB 后写回。
写操作不直接写 Redis，由调用方在 DB 写完后 DEL 缓存 key。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from forge.infrastructure.cache.redis_client import RedisClient

logger = logging.getLogger(__name__)

SESSION_KEY_PREFIX = "forge:session"
USER_SESSIONS_KEY_PREFIX = "forge:user_sessions"
SESSION_TTL = 3600


class SessionCache:
    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    # ---- Session 元数据 ----
    def _session_key(self, session_id: str) -> str:
        return f"{SESSION_KEY_PREFIX}:{session_id}"

    def _user_sessions_key(self, user_id: str) -> str:
        return f"{USER_SESSIONS_KEY_PREFIX}:{user_id}"

    async def get_session(self, session_id: str) -> dict[str, str] | None:
        """读取缓存的 session 元数据。未命中返回 None。"""
        data = await self._redis.hgetall(self._session_key(session_id))
        if data:
            return data
        return None

    async def set_session(self, session_id: str, data: dict[str, Any]) -> None:
        """写入 session 元数据缓存。"""
        flat = {k: (json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v)
                for k, v in data.items()}
        await self._redis.hset(self._session_key(session_id), flat, ttl=SESSION_TTL)

    async def invalidate_session(self, session_id: str) -> None:
        """失效 session 缓存。"""
        await self._redis.delete(self._session_key(session_id))

    # ---- 用户会话列表 ----
    async def get_user_sessions(self, user_id: str, limit: int = 50) -> list[str] | None:
        """读取缓存的用户会话 ID 列表。未命中返回 None。"""
        ids = await self._redis.zrevrange(
            self._user_sessions_key(user_id), 0, limit - 1
        )
        return ids

    async def add_user_session(self, user_id: str, session_id: str, score: float) -> None:
        """将 session 加入用户会话列表 ZSet。"""
        await self._redis.zadd(
            self._user_sessions_key(user_id),
            {session_id: score},
            ttl=SESSION_TTL,
        )

    async def remove_user_session(self, user_id: str, session_id: str) -> None:
        """从用户会话列表中移除。"""
        # 用 zrem 或直接 inval 整个 ZSet
        await self._redis.delete(self._user_sessions_key(user_id))
