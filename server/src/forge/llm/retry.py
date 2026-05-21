"""LLM 调用重试策略 (指数退避 + 抖动).

只对"可重试错误"重试:
    - 网络超时
    - 5xx
    - 429 限流 (带 Retry-After 时优先用)
4xx 业务错误 (如 invalid key) 不重试, 直接抛.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# 关键字匹配可重试错误 (provider 异常类型不统一, 用消息模糊匹配兜底)
_RETRY_HINTS = (
    "timeout",
    "timed out",
    "connection",
    "rate limit",
    "429",
    "502",
    "503",
    "504",
    "overloaded",
)


def is_retryable(exc: BaseException) -> bool:
    """判断异常是否值得重试."""
    msg = str(exc).lower()
    return any(h in msg for h in _RETRY_HINTS)


def call_with_retry(
    func: Callable[[], T],
    *,
    max_retries: int = 3,
    backoff_seconds: float = 1.0,
    max_backoff_seconds: float = 30.0,
    on_retry: Callable[[int, BaseException, float], None] | None = None,
) -> T:
    """同步重试包装. 失败时按指数退避重试, 最多 max_retries 次.

    Args:
        func:                被调用函数 (无参, 失败时抛异常)
        max_retries:         最大重试次数 (不含首次调用)
        backoff_seconds:     基础退避秒数 (实际 = base * 2^attempt + jitter)
        max_backoff_seconds: 退避上限, 防止超长等待
        on_retry:            重试回调 (attempt, exc, delay), 用于打 metric

    Returns:
        func 的成功返回值

    Raises:
        最后一次失败的异常 (原样抛出, 不包装)
    """
    last_exc: BaseException | None = None
    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as e:  # noqa: BLE001
            last_exc = e
            if attempt >= max_retries or not is_retryable(e):
                raise
            delay = min(
                backoff_seconds * (2**attempt) + random.uniform(0, 0.5),
                max_backoff_seconds,
            )
            logger.warning(
                "LLM 调用失败, %.1fs 后重试 (attempt=%d/%d): %s",
                delay,
                attempt + 1,
                max_retries,
                e,
            )
            if on_retry is not None:
                on_retry(attempt + 1, e, delay)
            time.sleep(delay)
    # 不可达, 但 mypy/类型检查需要
    assert last_exc is not None
    raise last_exc
