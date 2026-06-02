"""流式辅助工具: 多层超时 + 背压.

提供:
    - TimeoutConfig: 三级超时 (connection / first_token / total)
    - stream_with_first_token_timeout: 首 Token 超时保护, 超时抛
      FirstTokenTimeoutError 让 LLMDispatcher 切换下一个 entry

设计:
    - 首 Token 超时仅在第一个 chunk 之前生效, 一旦开始 yield 不再监听
    - FirstTokenTimeoutError 不在 retry hints 关键词列表中, 重试器不会重试,
      但 dispatcher 可识别它继续 fallback 到下一个 entry
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Iterator
from dataclasses import dataclass
from typing import TypeVar

from .providers.base import ChatChunk

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class TimeoutConfig:
    """三层超时配置.

    优先级 (从最先触发的角度):
        connection_timeout_s   TCP 建连 (provider SDK 内部, 此处仅作配置传递)
        first_token_timeout_s  流式: 等第一个 chunk 的最大时间
        total_timeout_s        整体响应完成的最大时间
    """

    connection_timeout_s: float = 5.0
    first_token_timeout_s: float = 15.0
    total_timeout_s: float = 120.0


class FirstTokenTimeoutError(TimeoutError):
    """流式响应首 Token 超时.

    可 fallback (切换到下一个 entry) 但不可重试 (同一 entry 重试通常无意义,
    多半是 provider 端拥塞).
    """


async def _next_sync_or_async(iterator: Iterator[T] | AsyncIterator[T]) -> T:
    """从同步/异步迭代器拿下一个元素 (兼容 dispatcher 当前的混合实现)."""
    if isinstance(iterator, AsyncIterator):
        return await iterator.__anext__()
    try:
        return next(iterator)
    except StopIteration as e:
        raise StopAsyncIteration from e


async def stream_with_first_token_timeout(
    stream: Iterator[ChatChunk] | AsyncIterator[ChatChunk],
    first_token_timeout_s: float,
) -> AsyncIterator[ChatChunk]:
    """首 Token 超时保护.

    在第一个 chunk 之前应用超时; 第一个 chunk 出来后剩余 chunk 直通.
    超时抛 FirstTokenTimeoutError.
    """
    try:
        first = await asyncio.wait_for(
            _next_sync_or_async(stream),
            timeout=first_token_timeout_s,
        )
    except TimeoutError as e:
        raise FirstTokenTimeoutError(
            f"首 Token 超时: {first_token_timeout_s:.1f}s 内未收到响应"
        ) from e
    except (StopIteration, StopAsyncIteration):
        # 空流也算"未拿到首 Token", 但不是超时, 让上层按 empty_stream 处理
        raise

    yield first
    if isinstance(stream, AsyncIterator):
        async for chunk in stream:
            yield chunk
    else:
        for chunk in stream:
            yield chunk


async def with_total_timeout(
    coro: Awaitable[T],
    total_timeout_s: float,
) -> T:
    """非流式调用的总超时包装."""
    try:
        return await asyncio.wait_for(coro, timeout=total_timeout_s)
    except TimeoutError as e:
        raise TimeoutError(
            f"LLM 调用总超时: {total_timeout_s:.1f}s 内未完成"
        ) from e


def replay_as_chunks(
    content: str,
    *,
    chunk_size: int = 40,
    usage: dict | None = None,
    finish_reason: str = "stop",
) -> list[ChatChunk]:
    """把整段 content 切片为多个 ChatChunk, 用于流式缓存回放.

    - 前 N-1 个 chunk: delta=切片, finish_reason=None
    - 最后 1 个 chunk: delta=最后切片, finish_reason=stop, usage=usage
    - content 空时仍至少返回一个最终空 chunk, 保证调用方拿到 finish_reason
    """
    if not content:
        return [ChatChunk(delta="", finish_reason=finish_reason, usage=usage)]
    pieces = [
        content[i : i + chunk_size] for i in range(0, len(content), chunk_size)
    ]
    chunks: list[ChatChunk] = []
    for idx, piece in enumerate(pieces):
        is_last = idx == len(pieces) - 1
        chunks.append(
            ChatChunk(
                delta=piece,
                finish_reason=finish_reason if is_last else None,
                usage=usage if is_last else None,
            )
        )
    return chunks


__all__ = [
    "FirstTokenTimeoutError",
    "TimeoutConfig",
    "replay_as_chunks",
    "stream_with_first_token_timeout",
    "with_total_timeout",
]
