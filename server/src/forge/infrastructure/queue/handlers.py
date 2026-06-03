"""本地任务队列的任务处理器注册表."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

TaskHandler = Callable[..., Any]


async def handle_memory_summarize(
    *,
    session_id: str,
    workspace_id: str | None = None,
) -> None:
    """本地执行会话摘要任务."""
    from forge.memory.tasks.summarize import run_summarize_task

    await run_summarize_task(session_id, workspace_id=workspace_id)


async def handle_cost_flush() -> int:
    """本地执行成本 flush 任务."""
    from forge.observability.cost.tasks.flush_cost import run_flush_cost_task

    return await run_flush_cost_task()


async def handle_context_digest(*, session_id: str) -> None:
    """本地执行会话 digest 任务."""
    from forge.context_mgmt.digest.tasks import run_digest_task

    await run_digest_task(session_id)


async def handle_context_embedding(*, session_id: str) -> None:
    """本地执行会话消息 embedding 冷路径任务 (语义召回)."""
    from forge.context_mgmt.recall.tasks import run_embedding_task

    await run_embedding_task(session_id)


_DEFAULT_HANDLERS: dict[str, TaskHandler] = {
    "memory.summarize": handle_memory_summarize,
    "observability.cost.flush": handle_cost_flush,
    "context.digest": handle_context_digest,
    "context.embedding": handle_context_embedding,
}


def resolve_task_handler(task_name: str) -> TaskHandler | None:
    return _DEFAULT_HANDLERS.get(task_name)
