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
TASK_EXTRACT_FACTS = "memory.extract_facts"


def install_memory_hooks(
    bus: EventBus,
    queue: TaskQueue,
    *,
    every_n_turns: int = 10,
    enabled: bool = True,
    facts_enabled: bool = False,
    extract_every_n_turns: int = 5,
) -> None:
    """注册 memory 模块对事件总线的所有订阅.

    Args:
        bus: 已初始化的 EventBus.
        queue: 已初始化的 TaskQueue (摘要/抽取任务通过此派发).
        every_n_turns: 每 N 轮触发一次摘要 (1 轮 = 1 user + 1 assistant = 2 条).
        enabled: False 时本函数 no-op (memory 系统整体关闭).
        facts_enabled: 是否同时派发事实抽取任务 (用户长期记忆写路径).
        extract_every_n_turns: 每 N 轮触发一次事实抽取.
    """
    if not enabled:
        logger.info("记忆 hooks 已关闭, 跳过订阅")
        return

    # 1 轮 = 2 条消息; 阈值 <= 0 表示对应任务不触发
    summarize_threshold = every_n_turns * 2 if every_n_turns > 0 else 0
    extract_threshold = (
        extract_every_n_turns * 2 if (facts_enabled and extract_every_n_turns > 0) else 0
    )
    if summarize_threshold <= 0 and extract_threshold <= 0:
        logger.warning("摘要与事实抽取阈值均未启用, 不订阅 turn.completed")
        return

    async def on_turn_completed(payload: EventPayload) -> None:
        session_id = payload.get("session_id")
        if not session_id:
            logger.warning("turn.completed payload 缺 session_id, 跳过")
            return
        user_id = payload.get("user_id")
        if user_id is not None and not isinstance(user_id, str):
            user_id = None

        # 本地 import, 避免 hooks 模块在 worker / 测试场景无端拉 DB 依赖.
        from forge.infrastructure.database.database import session_scope
        from forge.infrastructure.database.repositories.chat_message_repo import (
            ChatMessageRepository,
        )

        try:
            async with session_scope() as db:
                repo = ChatMessageRepository(db)
                count = await repo.count_by_session(session_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "memory hook 查询 message count 失败 (session=%s): %s",
                session_id,
                exc,
            )
            return
        if count == 0:
            return

        # 摘要与抽取共用同一次 count 查询, 各自按阈值判断派发
        if summarize_threshold and count % summarize_threshold == 0:
            logger.info(
                "memory hook 派发摘要任务: session=%s message_count=%d threshold=%d",
                session_id,
                count,
                summarize_threshold,
            )
            try:
                queue.submit(TASK_SUMMARIZE, session_id=session_id)
            except Exception as exc:  # noqa: BLE001
                logger.exception("memory hook 派发摘要任务失败: %s", exc)

        if extract_threshold and count % extract_threshold == 0:
            if not user_id:
                logger.warning(
                    "turn.completed payload 缺 user_id, 跳过事实抽取 session=%s",
                    session_id,
                )
                return
            logger.info(
                "memory hook 派发事实抽取任务: session=%s user=%s message_count=%d threshold=%d",
                session_id,
                user_id,
                count,
                extract_threshold,
            )
            try:
                queue.submit(TASK_EXTRACT_FACTS, session_id=session_id, user_id=user_id)
            except Exception as exc:  # noqa: BLE001
                logger.exception("memory hook 派发事实抽取任务失败: %s", exc)

    bus.subscribe(EVENT_TURN_COMPLETED, on_turn_completed)
    logger.info(
        "记忆 hooks 已安装: 摘要每 %d 条消息 / 事实抽取每 %s 条消息触发",
        summarize_threshold,
        extract_threshold if extract_threshold else "<关闭>",
    )
