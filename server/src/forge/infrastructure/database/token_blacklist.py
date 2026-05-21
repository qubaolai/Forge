"""Refresh token 黑名单 (in-memory 实现, 取代原 Redis 版本).

单机模式下没有 Redis. 拉黑列表落进程内存:
    - 进程重启 -> 黑名单清空, 已登出的用户需要重新登录 (可接受).
    - 进程内多协程通过 ``asyncio.Lock`` 串行写, 读不加锁.
    - 过期 jti 由 ``cleanup_expired()`` 在每次写入时顺手清掉,
      没有独立 GC 任务.

如需跨进程 / 持久化:
    - 落 ``tasks.db`` 加一张 ``refresh_token_blacklist(jti, expires_at)`` 表,
      实现接口不变.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends


class TokenBlacklist:
    """jti -> expires_at 的进程内黑名单."""

    def __init__(self) -> None:
        self._store: dict[str, datetime] = {}
        self._lock = asyncio.Lock()

    async def revoke(self, jti: str, expires_at: datetime) -> None:
        """把 ``jti`` 拉黑直到 ``expires_at``."""
        async with self._lock:
            self._store[jti] = expires_at
            self._cleanup_expired_locked()

    async def is_revoked(self, jti: str) -> bool:
        """查 ``jti`` 是否在黑名单内 (且未过期)."""
        exp = self._store.get(jti)
        if exp is None:
            return False
        if exp <= datetime.now(UTC):
            # 过期了, 顺手清掉; 不持锁也无所谓 (最差是下次再清一次)
            self._store.pop(jti, None)
            return False
        return True

    def _cleanup_expired_locked(self) -> None:
        now = datetime.now(UTC)
        expired = [k for k, v in self._store.items() if v <= now]
        for k in expired:
            self._store.pop(k, None)


# 全局单例
_INSTANCE = TokenBlacklist()


def get_token_blacklist() -> TokenBlacklist:
    return _INSTANCE


TokenBlacklistDep = Annotated[TokenBlacklist, Depends(get_token_blacklist)]
