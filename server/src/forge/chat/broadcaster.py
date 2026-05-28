"""内存事件广播器 (单 turn 多订阅者).

设计:
    - publish 是 fire-and-forget: 写入所有订阅者的队列, 失败不阻塞 (慢消费者会被丢)
    - subscribe 返回 asyncio.Queue, 调用方负责 unsubscribe (用 try/finally)
    - close 时给所有订阅者推 None sentinel, 订阅方据此结束循环
    - 数据可靠性不靠这个 (events.jsonl 才是 source of truth);
      这里只是实时性优化, 掉了订阅者从 events.jsonl 重读即可

容量:
    - 默认每订阅者 maxsize=1024. 超过即丢弃最旧 + 标记 dropped (由订阅方决定是否
      退回 jsonl 回放).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# 默认订阅者队列大小. 一次回答最多几千个 delta, 1024 足够正常使用.
DEFAULT_QUEUE_SIZE = 1024

# 推到订阅者队列的 sentinel, 表示流结束.
_CLOSE_SENTINEL: dict[str, Any] = {"__broadcaster_closed__": True}


def is_close_sentinel(item: dict[str, Any] | None) -> bool:
    return item is None or (
        isinstance(item, dict) and item.get("__broadcaster_closed__") is True
    )


class BroadcasterSubscriber:
    """订阅者句柄. 用 async for 迭代; 结束 / 异常时调用方需 unsubscribe."""

    def __init__(self, broadcaster: Broadcaster, queue: asyncio.Queue) -> None:
        self._broadcaster = broadcaster
        self._queue = queue
        self._closed = False
        self.dropped_count = 0

    async def __aiter__(self):
        while True:
            item = await self._queue.get()
            if is_close_sentinel(item):
                return
            yield item

    def unsubscribe(self) -> None:
        if self._closed:
            return
        self._broadcaster._remove_subscriber(self)
        self._closed = True

    def _mark_dropped(self) -> None:
        self.dropped_count += 1


class Broadcaster:
    """单 turn 的事件广播器."""

    def __init__(self, *, queue_size: int = DEFAULT_QUEUE_SIZE) -> None:
        self._subscribers: list[BroadcasterSubscriber] = []
        self._closed = False
        self._queue_size = queue_size

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @property
    def closed(self) -> bool:
        return self._closed

    def subscribe(self) -> BroadcasterSubscriber:
        """注册新订阅者. 若广播器已 close, 返回的订阅者立刻得到 sentinel."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        sub = BroadcasterSubscriber(self, queue)
        if self._closed:
            try:
                queue.put_nowait(_CLOSE_SENTINEL)
            except asyncio.QueueFull:
                pass
            return sub
        self._subscribers.append(sub)
        return sub

    async def publish(self, event: dict[str, Any]) -> None:
        """异步推到所有订阅者. 队列满 → 丢最旧 + dropped_count++ + 推新.

        永不抛, 永不阻塞.
        """
        if self._closed:
            return
        for sub in list(self._subscribers):
            try:
                sub._queue.put_nowait(event)
            except asyncio.QueueFull:
                # 慢消费者: 丢一个最旧的, 推入新的; 标记 dropped 让订阅方知情
                try:
                    sub._queue.get_nowait()
                    sub._mark_dropped()
                    sub._queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    sub._mark_dropped()
                    logger.warning(
                        "broadcaster 订阅者队列异常, 跳过事件 type=%s",
                        event.get("type"),
                    )

    def close(self) -> None:
        """关闭广播器: 给所有订阅者推 sentinel, 后续 subscribe 立即得到 sentinel."""
        if self._closed:
            return
        self._closed = True
        for sub in list(self._subscribers):
            try:
                sub._queue.put_nowait(_CLOSE_SENTINEL)
            except asyncio.QueueFull:
                # 极端: 队列还堵着. 强清一次再推
                try:
                    while True:
                        sub._queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    sub._queue.put_nowait(_CLOSE_SENTINEL)
                except asyncio.QueueFull:
                    pass
        # 不立即清空 _subscribers — 让 unsubscribe 自己清, 避免迭代时修改

    def _remove_subscriber(self, sub: BroadcasterSubscriber) -> None:
        try:
            self._subscribers.remove(sub)
        except ValueError:
            pass


__all__ = ["Broadcaster", "BroadcasterSubscriber"]
