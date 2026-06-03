"""语义召回模块的事件订阅 (turn.completed -> 派发 embedding 冷路径任务).

与 digest hooks 并列: 各自独立订阅同一 turn.completed, 互不依赖。
默认关闭 (semantic_recall.enabled=False) 时 no-op, 不产生任何额外负载。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from forge.infrastructure.event_bus.base import EventBus, EventPayload
    from forge.infrastructure.queue.base import TaskQueue

logger = logging.getLogger(__name__)

EVENT_TURN_COMPLETED = "turn.completed"
TASK_EMBEDDING = "context.embedding"


def install_embedding_hooks(
    bus: EventBus,
    queue: TaskQueue,
    *,
    enabled: bool = False,
) -> None:
    """注册语义召回对 turn.completed 的订阅 (冷路径补算消息向量)。

    Args:
        bus: 已初始化的 EventBus。
        queue: 已初始化的 TaskQueue。
        enabled: False 时 no-op (语义召回关闭)。
    """
    if not enabled:
        logger.info("语义召回 hooks 已关闭, 跳过订阅")
        return

    async def on_turn_completed(payload: EventPayload) -> None:
        session_id = payload.get("session_id")
        if not session_id:
            return
        try:
            queue.submit(TASK_EMBEDDING, session_id=session_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("embedding hook 派发任务失败: %s", exc)

    bus.subscribe(EVENT_TURN_COMPLETED, on_turn_completed)
    logger.info("语义召回 hooks 已安装: 每轮 turn.completed 派发 context.embedding")
