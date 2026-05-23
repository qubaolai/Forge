"""Message 缓存层 — Redis List。

Key 结构:
    forge:msgs:{session_business_id}  → List  TTL 1800s（最近 30 条 JSON）

策略: 消息变更时整个 List 失效。读未命中返回 None，调用方查 DB 后写回。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from forge.infrastructure.cache.redis_client import RedisClient

logger = logging.getLogger(__name__)

MSG_LIST_KEY_PREFIX = "forge:msgs"
MSG_TTL = 1800
MSG_MAX_CACHED = 30


class MessageCache:
    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    def _key(self, session_id: str) -> str:
        return f"{MSG_LIST_KEY_PREFIX}:{session_id}"

    async def get_recent(self, session_id: str) -> list[dict] | None:
        """读取缓存的最近消息列表。未命中返回 None。"""
        raw_list = await self._redis.lrange(self._key(session_id), 0, -1)
        if raw_list is None:
            return None
        result = []
        for raw in raw_list:
            try:
                result.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
        return result or None

    async def set_recent(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        """写入最近消息列表缓存。"""
        key = self._key(session_id)
        # 先删再写，保证一致性
        await self._redis.delete(key)
        for msg in messages[-MSG_MAX_CACHED:]:
            await self._redis.rpush(
                key, json.dumps(msg, ensure_ascii=False), ttl=MSG_TTL
            )

    async def invalidate(self, session_id: str) -> None:
        """消息变更时全量失效。"""
        await self._redis.delete(self._key(session_id))
