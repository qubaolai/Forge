"""舱壁隔离: 每个 provider 独立信号量, 防止单慢 provider 占满所有并发槽.

设计:
    - 按 provider 名 (chat client.provider_name) 分桶
    - 每桶一个 asyncio.Semaphore, 上限 max_concurrent_per_provider
    - 超出立即抛 BulkheadRejectError (而不是无限期等待), 让 dispatcher 切下一个 entry

用法 (LLMDispatcher 内):
    async with bulkhead.guard(provider_name):
        result = await client.chat(...)

不启用时 (max_concurrent_per_provider 设为 0 或 None): guard() 直接放行, 零开销.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class BulkheadRejectError(Exception):
    """舱壁拒绝: provider 已达到最大并发数."""


class ProviderBulkhead:
    """per-provider 并发限制.

    Phase 4: 进程内信号量, 进程间不共享 (与 circuit breaker 同).
    """

    def __init__(self, max_concurrent_per_provider: int = 20) -> None:
        self._max = max_concurrent_per_provider
        # 自己维护 in_flight 计数, 避免依赖 asyncio.Semaphore._value 私有属性
        self._in_flight: dict[str, int] = {}
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self._max > 0

    @asynccontextmanager
    async def guard(self, provider: str) -> AsyncIterator[None]:
        """并发槽 RAII (非阻塞 try-acquire): 超出立即抛 BulkheadRejectError."""
        if not self.enabled:
            yield
            return
        acquired = False
        async with self._lock:
            cur = self._in_flight.get(provider, 0)
            if cur >= self._max:
                raise BulkheadRejectError(
                    f"provider={provider} 已达最大并发 {self._max}"
                )
            self._in_flight[provider] = cur + 1
            acquired = True
        try:
            yield
        finally:
            if acquired:
                async with self._lock:
                    self._in_flight[provider] = max(
                        0, self._in_flight.get(provider, 1) - 1
                    )

    def in_flight(self, provider: str) -> int:
        return self._in_flight.get(provider, 0)

    def reset(self) -> None:
        """测试用: 清空所有计数."""
        self._in_flight.clear()


# 全局单例
_bulkhead: ProviderBulkhead | None = None


def get_bulkhead() -> ProviderBulkhead:
    global _bulkhead
    if _bulkhead is None:
        _bulkhead = ProviderBulkhead(max_concurrent_per_provider=20)
    return _bulkhead


def set_bulkhead(bulkhead: ProviderBulkhead) -> None:
    """由 lifespan 在配置加载后注入."""
    global _bulkhead
    _bulkhead = bulkhead


__all__ = [
    "BulkheadRejectError",
    "ProviderBulkhead",
    "get_bulkhead",
    "set_bulkhead",
]
