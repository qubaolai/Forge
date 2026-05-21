"""会话摘要 Celery 任务.

实际业务逻辑在 memory.summary.service.SummaryService, 本模块只是把它包成
Celery task + 处理重试.

任务签名:
    memory.summarize(session_id: str, workspace_id: str | None = None) -> None

由 hooks.py 在 turn.completed 事件 + 触发条件满足时派发.
ContextAssembler 的主动压缩走同一个 service, 不走 Celery.

celery 可选依赖: 没装 celery 时, 整个模块 import 仍然可工作 (用兜底装饰器).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# celery 装饰器: 软依赖
# ---------------------------------------------------------------------------
try:
    from celery import shared_task
except ImportError:  # pragma: no cover

    def shared_task(*args: Any, **kwargs: Any):
        def decorator(fn):
            return fn

        if args and callable(args[0]):
            return args[0]
        return decorator


# ---------------------------------------------------------------------------
# 本地/可复用入口 (async)
# ---------------------------------------------------------------------------
async def run_summarize_task(
    session_id: str,
    workspace_id: str | None = None,
) -> None:
    """执行摘要任务核心逻辑.

    由本地队列与 Celery wrapper 共用, 避免两套实现分叉.
    """
    from forge.memory.summary.service import get_summary_service

    await get_summary_service().summarize_session(session_id, workspace_id=workspace_id)


# ---------------------------------------------------------------------------
# 任务入口 (sync, Celery 标准签名)
# ---------------------------------------------------------------------------
@shared_task(name="memory.summarize", bind=True, max_retries=3, default_retry_delay=30)
def summarize_task(
    self: Any,
    session_id: str,
    workspace_id: str | None = None,
) -> None:
    """生成 + 持久化 session_id 的摘要.

    重试:
        - 最多 3 次, 间隔 30s
        - 基础设施异常 (DB / LLM 网络错) -> 走 retry
        - 业务异常 (LLM 返回空 / 没有历史) -> service 内部返回 None, 不进入这条路径
    """
    from forge.memory.summary.service import (
        InfrastructureError,
    )

    logger.info(
        "摘要任务开始 session=%s workspace=%s 第 %s 次尝试",
        session_id,
        workspace_id or "<none>",
        self.request.retries,
    )
    try:
        asyncio.run(run_summarize_task(session_id, workspace_id=workspace_id))
        logger.info("摘要任务完成 session=%s workspace=%s", session_id, workspace_id or "<none>")
    except InfrastructureError as exc:
        logger.warning("摘要任务可重试错误: %s", exc)
        raise self.retry(exc=exc) from exc
