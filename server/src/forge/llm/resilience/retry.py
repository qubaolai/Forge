"""LLM 调用重试策略 (指数退避 + 抖动 + 429 Retry-After).

只对"可重试错误"重试:
    - 网络超时
    - 5xx
    - 429 限流 (优先用 Retry-After)
4xx 业务错误 (如 invalid key) 不重试, 直接抛.

Strategy 抽象 (per-provider 差异化):
    - RetryPolicy: 调用方依赖的抽象, 决定"这个异常要不要重试 / 等多久"
    - KeywordRetryPolicy: 默认实现, 按异常 msg 关键词匹配
    - 后续可加 ProviderAwareRetryPolicy 等, 继承同一 ABC, 调用方零改动.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_RETRY_HINTS = (
    "timeout", "timed out", "connection",
    "rate limit", "429", "502", "503", "504", "overloaded",
)


def is_retryable(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(h in msg for h in _RETRY_HINTS)


def is_rate_limit(exc: BaseException) -> bool:
    """判断是否是 429 限流错误."""
    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg or "rate_limit" in msg


def parse_retry_after(exc: BaseException) -> float | None:
    """从异常中提取 Retry-After 秒数. 支持 header / body JSON 格式."""
    msg = str(exc)
    m = re.search(r"retry[-_]after[:\s]+(\d+)", msg, re.IGNORECASE)
    if m:
        return float(m.group(1))
    m = re.search(r"retry after (\d+)\s*seconds?", msg, re.IGNORECASE)
    if m:
        return float(m.group(1))
    m = re.search(r"try again in (\d+)\s*s", msg, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


@dataclass
class RetryResult:
    success: bool
    is_rate_limited: bool = False
    retry_after_seconds: float | None = None


class RetryPolicy(ABC):
    """重试策略接口."""

    max_attempts: int

    @abstractmethod
    def is_retryable(self, exc: BaseException) -> bool: ...

    @abstractmethod
    def backoff_delay(self, attempt: int, exc: BaseException) -> float:
        """返回第 attempt 次重试前应该等待的秒数."""
        ...


@dataclass
class KeywordRetryPolicy(RetryPolicy):
    """默认重试策略: 关键词匹配 + 指数退避 + Retry-After 解析."""

    max_attempts: int = 3
    """最多重试次数 (不含首次调用)"""

    backoff_seconds: float = 1.0
    """基础退避秒数, 实际延迟为 backoff_seconds * 2^attempt + jitter"""

    max_backoff_seconds: float = 30.0

    def is_retryable(self, exc: BaseException) -> bool:
        return is_retryable(exc)

    def backoff_delay(self, attempt: int, exc: BaseException) -> float:
        retry_after = parse_retry_after(exc)
        if retry_after is not None:
            return min(retry_after + random.uniform(0, 1), self.max_backoff_seconds)
        return min(
            self.backoff_seconds * (2 ** attempt) + random.uniform(0, 0.5),
            self.max_backoff_seconds,
        )


async def call_with_retry(
    func: Callable[[], Awaitable[T]],
    *,
    max_retries: int = 3,
    backoff_seconds: float = 1.0,
    max_backoff_seconds: float = 30.0,
    on_retry: Callable[[int, BaseException, float], bool | None] | None = None,
    policy: RetryPolicy | None = None,
) -> T:
    """带重试的异步调用.

    `policy` 优先, 提供时其它 max_retries / backoff_seconds 参数被忽略.
    未提供 policy 时按传入参数构造一个临时 KeywordRetryPolicy (保持兼容).
    """
    if policy is None:
        policy = KeywordRetryPolicy(
            max_attempts=max_retries,
            backoff_seconds=backoff_seconds,
            max_backoff_seconds=max_backoff_seconds,
        )
    last_exc: BaseException | None = None
    for attempt in range(policy.max_attempts + 1):
        try:
            return await func()
        except Exception as e:
            last_exc = e
            if attempt >= policy.max_attempts or not policy.is_retryable(e):
                raise
            delay = policy.backoff_delay(attempt, e)
            if on_retry is not None:
                should_continue = on_retry(attempt + 1, e, delay)
                if should_continue is False:
                    raise
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc


__all__ = [
    "KeywordRetryPolicy",
    "RetryPolicy",
    "RetryResult",
    "call_with_retry",
    "is_rate_limit",
    "is_retryable",
    "parse_retry_after",
]
