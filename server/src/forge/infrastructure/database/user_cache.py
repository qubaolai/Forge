"""User 缓存 (in-memory 实现, 取代原 Redis 版本).

单机模式下没有 Redis. 使用 ``cachetools.TTLCache`` 在进程内缓存
用户对象, 减少每次请求都打 DB.

特性:
    - TTL: 默认 300s (与原 Redis cache 一致)
    - 容量: 默认 1024 (单机用户量极小, 实际只缓存当前登录用户 1 个)
    - 失败回源: cache miss -> 读 DB, 写回 cache; DB 失败抛错
    - 失效语义: ``invalidate(user_id)`` 显式清, 登出 / 改密码后调

线程安全:
    - ``cachetools.TTLCache`` 本身非线程安全, 这里加 ``asyncio.Lock`` 保护写;
      读直接走 dict, 偶尔的并发读不会有正确性问题 (拿到的 user 对象都是不可变快照).
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from cachetools import TTLCache
from fastapi import Depends

from forge.api.dependencies import DbSession
from forge.infrastructure.database.orm.user_orm import UserOrm
from forge.infrastructure.database.repositories.user_repo import UserRepository


class UserCache:
    """user_id -> UserOrm 的进程内 TTL 缓存."""

    _store: TTLCache[str, UserOrm] = TTLCache(maxsize=1024, ttl=300)
    _lock = asyncio.Lock()

    def __init__(self, repo: UserRepository) -> None:
        self._repo = repo

    async def get(self, user_id: str) -> UserOrm | None:
        """先查缓存, miss 则回源 DB. DB 错误向上传播."""
        cached = self._store.get(user_id)
        if cached is not None:
            return cached
        user = await self._repo.get_by_id(user_id)
        if user is not None:
            async with self._lock:
                self._store[user_id] = user
        return user

    async def invalidate(self, user_id: str) -> None:
        async with self._lock:
            self._store.pop(user_id, None)

    @classmethod
    def reset(cls) -> None:
        """测试用 — 清空全局缓存."""
        cls._store.clear()


def get_user_cache(db: DbSession) -> UserCache:
    return UserCache(UserRepository(db))


UserCacheDep = Annotated[UserCache, Depends(get_user_cache)]
