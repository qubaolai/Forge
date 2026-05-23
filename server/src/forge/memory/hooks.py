"""Memory 模块的事件订阅 (turn.completed -> 派发摘要任务).

设计:
    - install_memory_hooks(bus, queue, ...) 在 lifespan 启动期一次性调用.
    - 业务侧 (chat 路由) 只 publish "turn.completed", 不感知本模块存在.
    - hook 内自己查 DB 算 turn_count, 满足 every_n_turns 才 submit.

事件契约:
    name:    "turn.completed"
    payload: {
        "session_id": str,
        "user_id":    str,
        "trace_id":   str,
    }
    -- 故意不传 turn_count: chat 路由不应被迫提供这个, hook 自己算.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from forge.infrastructure.event_bus.base import EventBus, EventPayload
    from forge.infrastructure.queue.base import TaskQueue

logger = logging.getLogger(__name__)

EVENT_TURN_COMPLETED = "turn.completed"
TASK_SUMMARIZE = "memory.summarize"


def install_memory_hooks(
    bus: EventBus,
    queue: TaskQueue,
    *,
    every_n_turns: int = 10,
    enabled: bool = True,
) -> None:
    """注册 memory 模块对事件总线的所有订阅.

    Args:
        bus: 已初始化的 EventBus.
        queue: 已初始化的 TaskQueue (摘要任务通过此派发).
        every_n_turns: 每 N 轮触发一次摘要 (1 轮 = 1 user + 1 assistant = 2 条).
        enabled: False 时本函数 no-op (memory 系统整体关闭).
    """
    if not enabled:
        logger.info("记忆 hooks 已关闭, 跳过订阅")
        return

    if every_n_turns <= 0:
        logger.warning("memory.trigger.every_n_turns <= 0, 不订阅 turn.completed")
        return

    threshold_messages = every_n_turns * 2  # 1 轮 = 2 条消息

    async def on_turn_completed(payload: EventPayload) -> None:
        session_id = payload.get("session_id")
        if not session_id:
            logger.warning("turn.completed payload 缺 session_id, 跳过")
            return
        workspace_id = payload.get("workspace_id")
        if workspace_id is not None and not isinstance(workspace_id, str):
            workspace_id = None

        # 本地 import, 避免 hooks 模块在 worker / 测试场景无端拉 DB 依赖.
        from forge.infrastructure.database.database import (
            get_session_factory,
        )
        from forge.infrastructure.database.repositories.chat_message_repo import (
            ChatMessageRepository,
        )

        try:
            factory = get_session_factory()
            async with factory() as db:
                repo = ChatMessageRepository(db)
                count = await repo.count_by_session(session_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "memory hook 查询 message count 失败 (session=%s): %s",
                session_id,
                exc,
            )
            return

        if count == 0 or count % threshold_messages != 0:
            return

        logger.info(
            "memory hook 派发摘要任务: session=%s workspace=%s message_count=%d threshold=%d",
            session_id,
            workspace_id or "<none>",
            count,
            threshold_messages,
        )
        # workspace_id 为 None 时不传, 保持单机历史调用方对 kwargs 形态的预期.
        submit_kwargs: dict[str, str] = {"session_id": session_id}
        if workspace_id:
            submit_kwargs["workspace_id"] = workspace_id
        try:
            queue.submit(TASK_SUMMARIZE, **submit_kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.exception("memory hook 派发任务失败: %s", exc)

    bus.subscribe(EVENT_TURN_COMPLETED, on_turn_completed)
    logger.info(
        "记忆 hooks 已安装: 每 %d 轮 (= %d 条消息) 触发一次摘要",
        every_n_turns,
        threshold_messages,
    )
