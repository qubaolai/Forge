"""事件总线: Protocol + 进程内实现 + 全局单例工厂.

future: 跨进程场景 (Redis Pub/Sub / Kafka) 时, get_event_bus() 内部按 settings
分发到对应实现, 业务侧 import 不变.
"""

from __future__ import annotations

from forge.infrastructure.event_bus.base import (
    EventBus,
    EventHandler,
    EventPayload,
    Unsubscribe,
)
from forge.infrastructure.event_bus.in_process import InProcessEventBus

_DEFAULT_BUS: EventBus | None = None


def get_event_bus() -> EventBus:
    """返回全局 EventBus 单例 (Stage 2 固定 InProcessEventBus)."""
    global _DEFAULT_BUS
    if _DEFAULT_BUS is None:
        _DEFAULT_BUS = InProcessEventBus()
    return _DEFAULT_BUS


def reset_event_bus() -> None:
    """单测用: 清掉单例, 下次 get_event_bus() 重建."""
    global _DEFAULT_BUS
    _DEFAULT_BUS = None


__all__ = [
    "EventBus",
    "EventHandler",
    "EventPayload",
    "InProcessEventBus",
    "Unsubscribe",
    "get_event_bus",
    "reset_event_bus",
]
