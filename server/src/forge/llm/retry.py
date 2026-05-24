"""LLM 调用重试策略 (指数退避 + 抖动 + 429 Retry-After)。

只对"可重试错误"重试:
    - 网络超时
    - 5xx
    - 429 限流 (优先用 Retry-After)
4xx 业务错误 (如 invalid key) 不重试, 直接抛。
"""

from __future__ import annotations

import logging
import random
import re
import time
from collections.abc import Callable
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
    """判断是否是 429 限流错误。"""
    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg or "rate_limit" in msg


def parse_retry_after(exc: BaseException) -> float | None:
    """从异常中提取 Retry-After 秒数。支持 header 格式和 body JSON 格式。"""
    msg = str(exc)
    # Header: "Retry-After: 30"
    m = re.search(r"retry[-_]after[:\s]+(\d+)", msg, re.IGNORECASE)
    if m:
        return float(m.group(1))
    # OpenAI/DeepSeek body: "Please retry after 15 seconds"
    m = re.search(r"retry after (\d+)\s*seconds?", msg, re.IGNORECASE)
    if m:
        return float(m.group(1))
    # "try again in 30s"
    m = re.search(r"try again in (\d+)\s*s", msg, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


@dataclass
class RetryResult:
    success: bool
    is_rate_limited: bool = False
    retry_after_seconds: float | None = None


def call_with_retry(
    func: Callable[[], T],
    *,
    max_retries: int = 3,
    backoff_seconds: float = 1.0,
    max_backoff_seconds: float = 30.0,
    on_retry: Callable[[int, BaseException, float], bool | None] | None = None,
) -> T:
    last_exc: BaseException | None = None
    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as e:
            last_exc = e
            if attempt >= max_retries or not is_retryable(e):
                raise
            # 429 优先使用 Retry-After, 否则指数退避
            retry_after = parse_retry_after(e)
            if retry_after is not None:
                delay = min(retry_after + random.uniform(0, 1), max_backoff_seconds)
            else:
                delay = min(
                    backoff_seconds * (2 ** attempt) + random.uniform(0, 0.5),
                    max_backoff_seconds,
                )
            if on_retry is not None:
                should_continue = on_retry(attempt + 1, e, delay)
                if should_continue is False:
                    logger.warning(
                        "LLM 调用失败，本 key 不再重试 (attempt=%d/%d, rate_limited=%s): %s",
                        attempt + 1, max_retries, is_rate_limit(e), e,
                    )
                    raise
            logger.warning(
                "LLM 调用失败, %.1fs 后重试 (attempt=%d/%d, rate_limited=%s): %s",
                delay, attempt + 1, max_retries,
                retry_after is not None, e,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc
