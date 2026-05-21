"""EventBus 协议: 进程内 / 跨进程 都按这个接口暴露.

设计原则:
    - publish 不抛: 业务流程 publish 完就走, subscriber 抛错由 EventBus 自己吞掉
      + 记日志, 不能反噬 publisher (比如 _stream_chat 不能因 memory 模块 hang).
    - subscriber 是 async, 由 EventBus 决定怎么调度 (in-process 用
      asyncio.create_task; Redis Pub/Sub / Kafka 之后实现时是另一个 EventBus
      实现, 接口不变).
    - payload 是 plain dict: 跨进程实现要序列化时不被自定义类型卡住.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

EventPayload = dict[str, Any]
EventHandler = Callable[[EventPayload], Awaitable[None]]
Unsubscribe = Callable[[], None]


class EventBus(Protocol):
    """进程内 / 跨进程 事件总线."""

    def subscribe(self, event_name: str, handler: EventHandler) -> Unsubscribe:
        """注册 subscriber. 多次 subscribe 同名事件 -> 多个 handler 并行被调.

        返回一个 ``unsubscribe()`` 回调; 调用即移除该 handler. 对长期常驻
        subscriber (lifespan 注册) 可忽略返回值; 对短期 subscriber
        (SSE follow 单次连接) 必须在 finally 里调用, 避免泄漏.
        """
        ...

    async def publish(self, event_name: str, payload: EventPayload) -> None:
        """发布事件.

        - 不阻塞: subscriber 走 fire-and-forget 调度.
        - 不抛: subscriber 抛错被吞 + 记日志.
        """
        ...
