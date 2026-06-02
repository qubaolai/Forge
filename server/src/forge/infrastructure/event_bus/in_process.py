"""进程内 EventBus 实现.

publish 流程:
    1. 取该事件名下所有 subscriber.
    2. 每个 subscriber 用 asyncio.create_task 包成独立任务 (fire-and-forget).
    3. publish 立即返回.

subscriber 抛错:
    - 在 _safe_call 内被吞, 仅 logger.exception 记录.
    - 不影响其他 subscriber, 也不影响 publisher.

注意:
    - 跨进程场景 (worker 进程要收到 web 进程的事件) 需要换 RedisPubSubEventBus
      或 KafkaEventBus, 接口 (EventBus ABC) 保持不变.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading

from forge.infrastructure.event_bus.base import (
    EventBus,
    EventHandler,
    EventPayload,
    Unsubscribe,
)

logger = logging.getLogger(__name__)


class InProcessEventBus(EventBus):
    """进程内 pub/sub. 单实例, 用 RLock 保护 subscriber 表突变."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventHandler]] = {}
        self._guard = threading.RLock()

    def subscribe(self, event_name: str, handler: EventHandler) -> Unsubscribe:
        with self._guard:
            self._subscribers.setdefault(event_name, []).append(handler)
        logger.debug(
            "事件订阅注册 event=%s handler=%s",
            event_name,
            getattr(handler, "__qualname__", repr(handler)),
        )

        def _unsubscribe() -> None:
            with self._guard:
                handlers = self._subscribers.get(event_name)
                if not handlers:
                    return
                with contextlib.suppress(ValueError):
                    handlers.remove(handler)

        return _unsubscribe

    async def publish(self, event_name: str, payload: EventPayload) -> None:
        with self._guard:
            handlers = tuple(self._subscribers.get(event_name, ()))
        if not handlers:
            return
        # 不 gather, 用 create_task fire-and-forget: publisher 立即返回.
        for h in handlers:
            asyncio.create_task(_safe_call(event_name, h, payload))


async def _safe_call(event_name: str, handler: EventHandler, payload: EventPayload) -> None:
    try:
        await handler(payload)
    except Exception:
        logger.exception(
            "事件处理异常 event=%s handler=%s",
            event_name,
            getattr(handler, "__qualname__", repr(handler)),
        )
