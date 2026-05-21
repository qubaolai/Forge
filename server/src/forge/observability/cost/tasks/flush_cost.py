"""LLM 成本 flush Celery 任务.

签名: observability.cost.flush()

由 Celery beat 周期触发 (默认 60s), 把进程内 CostTracker 的 delta 追加到
cost.jsonl, 并刷新 db_baseline.

注意:
    - Web 进程和 Celery worker 进程各有独立 CostTracker. Beat 只会驱动 worker
      进程做自己的 flush. Web 进程的 flush 需要 lifespan 里挂背景循环
      或定期 POST /internal/flush_cost 这种方式触发 (后续接).
    - 本任务不接收任何 kwargs, 操作的是 worker 进程内的全局 tracker.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


try:
    from celery import shared_task
except ImportError:  # pragma: no cover

    def shared_task(*args: Any, **kwargs: Any):
        def decorator(fn):
            return fn

        if args and callable(args[0]):
            return args[0]
        return decorator


async def run_flush_cost_task() -> int:
    """执行成本 flush 核心逻辑.

    由本地队列与 Celery wrapper 共用, 避免两套实现分叉.
    """
    from forge.infrastructure.database.database import get_session_factory
    from forge.llm.cost_tracker import get_cost_tracker

    try:
        factory = get_session_factory()
    except RuntimeError:
        logger.warning("cost flush: session_factory 未初始化, 跳过")
        return 0
    if factory is None:  # 防御式兼容旧调用方 mock
        logger.warning("cost flush: session_factory 为空, 跳过")
        return 0
    return await get_cost_tracker().flush_to_db(factory)


@shared_task(name="observability.cost.flush", bind=True, max_retries=3, default_retry_delay=30)
def flush_cost_task(self: Any) -> int:
    """把 CostTracker 的内存 delta 写到 cost.jsonl.

    Returns:
        实际写入条目数.
    """
    try:
        return asyncio.run(run_flush_cost_task())
    except Exception as exc:  # noqa: BLE001
        logger.warning("cost flush 失败, 将重试: %s", exc)
        raise self.retry(exc=exc) from exc
