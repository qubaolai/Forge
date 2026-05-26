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
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self._max > 0

    async def _get(self, provider: str) -> asyncio.Semaphore:
        sem = self._semaphores.get(provider)
        if sem is not None:
            return sem
        async with self._lock:
            sem = self._semaphores.get(provider)
            if sem is not None:
                return sem
            sem = asyncio.Semaphore(self._max)
            self._semaphores[provider] = sem
            return sem

    @asynccontextmanager
    async def guard(self, provider: str) -> AsyncIterator[None]:
        """并发槽 RAII. 超出立即抛 BulkheadRejectError."""
        if not self.enabled:
            yield
            return
        sem = await self._get(provider)
        if not sem.locked() or sem._value > 0:  # noqa: SLF001
            # _value 是 asyncio.Semaphore 当前可用计数
            pass
        # 非阻塞获取: 没有可用槽就立即拒绝
        if sem._value <= 0:  # noqa: SLF001
            raise BulkheadRejectError(
                f"provider={provider} 已达最大并发 {self._max}"
            )
        await sem.acquire()
        try:
            yield
        finally:
            sem.release()

    def in_flight(self, provider: str) -> int:
        sem = self._semaphores.get(provider)
        if sem is None:
            return 0
        return max(0, self._max - sem._value)  # noqa: SLF001

    def reset(self) -> None:
        """测试用: 清空所有信号量."""
        self._semaphores.clear()


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
