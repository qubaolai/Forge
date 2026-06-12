"""事实抽取 Celery 任务.

实际业务逻辑在 memory.facts.service.FactExtractionService, 本模块只是把它
包成 Celery task + 处理重试 (镜像 tasks/summarize.py).

任务签名:
    memory.extract_facts(session_id: str, user_id: str) -> None

由 hooks.py 在 turn.completed 事件 + 抽取阈值满足时派发.

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
async def run_extract_facts_task(session_id: str, user_id: str) -> None:
    """执行事实抽取任务核心逻辑.

    由本地队列与 Celery wrapper 共用, 避免两套实现分叉.
    """
    from forge.memory.facts.service import get_fact_extraction_service

    await get_fact_extraction_service().extract_from_session(session_id, user_id)


# ---------------------------------------------------------------------------
# 任务入口 (sync, Celery 标准签名)
# ---------------------------------------------------------------------------
@shared_task(name="memory.extract_facts", bind=True, max_retries=3, default_retry_delay=30)
def extract_facts_task(self: Any, session_id: str, user_id: str) -> None:
    """抽取 + 持久化 session_id 中的用户长期事实.

    重试:
        - 最多 3 次, 间隔 30s
        - 基础设施异常 (DB / LLM 网络错) -> 走 retry (水位未推进, 重做幂等)
        - 业务情况 (无新消息 / 无可抽 / 全部去重) -> service 返回 0, 不进这条路径
    """
    from forge.memory.summary.service import InfrastructureError

    logger.info(
        "事实抽取任务开始 session=%s user=%s 第 %s 次尝试",
        session_id,
        user_id,
        self.request.retries,
    )
    try:
        asyncio.run(run_extract_facts_task(session_id, user_id))
        logger.info("事实抽取任务完成 session=%s user=%s", session_id, user_id)
    except InfrastructureError as exc:
        logger.warning("事实抽取任务可重试错误: %s", exc)
        raise self.retry(exc=exc) from exc
