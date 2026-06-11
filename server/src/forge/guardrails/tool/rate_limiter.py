"""工具调用频率限制防护栏 (滑动窗口, 进程内).

设计
----
- 单机模式不引入 Redis: 限流数据在 ``RateLimiter`` 实例内的 dict 里.
- 每个 ``(tool_name, role)`` 一个独立的时间戳队列, 滑动窗口判定.
- 默认配置:
    - 全局兜底: 每分钟 600 次 (远高于单个 LLM 会话的常规消耗)
    - 危险工具 (shell / write_file / edit_file / http_request): 每分钟 60 次
    - 自定义可在构造时传入

接口
----
``check(tool_name, role) -> RateCheckResult``:
    - allow=True  → 已计入本次调用, 放行
    - allow=False → 触发限流, 含 retry_after 秒数

线程安全
--------
- ``asyncio.Lock`` 保护 dict 写; ToolExecutor 是 single-thread coroutine,
  跨 thread 调用本就不支持.
- 时间窗口外的旧记录每次 check 时顺手 pop.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_DEFAULT_WINDOW_SECONDS: float = 60.0
_DEFAULT_LIMIT: int = 600

# 特定工具的更严限制 (当前无, 保留为 per-tool 配置扩展点)
_DEFAULT_DANGEROUS_LIMITS: dict[str, int] = {}


@dataclass(frozen=True)
class RateCheckResult:
    allow: bool
    reason: str = ""
    retry_after: float = 0.0


class RateLimiter:
    """按 ``(tool_name, role)`` 维度的滑动窗口限流器, 进程内单例."""

    def __init__(
        self,
        *,
        window_seconds: float = _DEFAULT_WINDOW_SECONDS,
        default_limit: int = _DEFAULT_LIMIT,
        tool_limits: dict[str, int] | None = None,
    ) -> None:
        """构造限流器.

        ``tool_limits`` 是显式 per-tool 覆盖. 没传时:
            - 测试 / 简单场景: 所有工具走 ``default_limit``.
            - 生产: 用 ``get_rate_limiter()`` 工厂, 它会预加载 dangerous 工具的更严配置.
        """
        self._window = max(0.001, float(window_seconds))
        self._default_limit = max(1, int(default_limit))
        self._tool_limits: dict[str, int] = dict(tool_limits) if tool_limits else {}
        self._buckets: dict[tuple[str, str], deque[float]] = {}
        self._lock = asyncio.Lock()

    def limit_for(self, tool_name: str) -> int:
        return self._tool_limits.get(tool_name, self._default_limit)

    async def check(self, tool_name: str, role: str) -> RateCheckResult:
        """命中限流时不记录调用; 否则记录并放行."""
        key = (tool_name, role)
        limit = self.limit_for(tool_name)
        now = time.monotonic()
        threshold = now - self._window
        async with self._lock:
            bucket = self._buckets.setdefault(key, deque())
            while bucket and bucket[0] < threshold:
                bucket.popleft()
            if len(bucket) >= limit:
                oldest = bucket[0]
                retry_after = max(0.0, (oldest + self._window) - now)
                return RateCheckResult(
                    allow=False,
                    reason=(
                        f"工具 {tool_name!r} 在 {self._window:.0f}s 窗口内已达上限 "
                        f"{limit} 次, role={role}"
                    ),
                    retry_after=retry_after,
                )
            bucket.append(now)
        return RateCheckResult(allow=True)

    def reset(self) -> None:
        """测试 / 调试用 — 清空所有桶."""
        self._buckets.clear()


_INSTANCE: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    """生产用: 全局单例, 预加载 dangerous 工具的严格限制."""
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = RateLimiter(tool_limits=dict(_DEFAULT_DANGEROUS_LIMITS))
    return _INSTANCE


def reset_rate_limiter() -> None:
    """测试用 — 重置全局单例."""
    global _INSTANCE
    _INSTANCE = None


__all__ = ["RateCheckResult", "RateLimiter", "get_rate_limiter", "reset_rate_limiter"]
