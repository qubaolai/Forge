"""digest 模块的事件订阅 (turn.completed -> 派发 digest 任务).

与 memory hooks 的差异 (有意为之):
    - memory 摘要是会话级、容忍延迟, 故按 every_n_turns 节流。
    - digest 是单条长消息级, 需尽快产出 (否则该消息接下来若干轮只能走廉价截断降级),
      故 **每轮** turn.completed 都入队; 重复由 task 内 source_hash 幂等去重兜底。

归属: 本 hook 属 context 子系统, 与 memory hook 各自独立订阅同一事件, 互不依赖。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from forge.infrastructure.event_bus.base import EventBus, EventPayload
    from forge.infrastructure.queue.base import TaskQueue

logger = logging.getLogger(__name__)

EVENT_TURN_COMPLETED = "turn.completed"
TASK_DIGEST = "context.digest"


def install_digest_hooks(
    bus: EventBus,
    queue: TaskQueue,
    *,
    enabled: bool = True,
) -> None:
    """注册 digest 模块对 turn.completed 的订阅。

    Args:
        bus: 已初始化的 EventBus。
        queue: 已初始化的 TaskQueue (digest 任务通过此派发)。
        enabled: False 时 no-op (digest 整体关闭)。
    """
    if not enabled:
        logger.info("digest hooks 已关闭, 跳过订阅")
        return

    async def on_turn_completed(payload: EventPayload) -> None:
        session_id = payload.get("session_id")
        if not session_id:
            return
        try:
            queue.submit(TASK_DIGEST, session_id=session_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("digest hook 派发任务失败: %s", exc)

    bus.subscribe(EVENT_TURN_COMPLETED, on_turn_completed)
    logger.info("digest hooks 已安装: 每轮 turn.completed 派发 context.digest")
