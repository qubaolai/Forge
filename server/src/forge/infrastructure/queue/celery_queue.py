"""Celery 后端的 TaskQueue 实现.

设计:
    - 业务侧只通过 CeleryTaskQueue.submit("memory.summarize", ...) 调度,
      不直接 import Celery 装饰器 -- 这样将来换 Temporal 时业务代码零改动.
    - Celery app 是 Stage 2 唯一可识别的 worker 进程入口:
          celery -A forge.infrastructure.queue.celery_queue:app worker
      task 文件 (eg memory/tasks/summarize.py) 用 @shared_task, 通过
      autodiscover 被 app 加载.

延迟导入:
    celery 是 optional extras (-E memory). 没装 celery 时直接 import 此模块会
    崩, 所以 build_celery_app 内部才 import; 模块顶部不 import celery.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from forge.infrastructure.queue.base import TaskQueue

if TYPE_CHECKING:  # 类型注解用, 运行期不依赖
    from celery import Celery

logger = logging.getLogger(__name__)


def build_celery_app(
    *,
    broker_url: str,
    backend_url: str,
    task_modules: list[str] | None = None,
    beat_schedule: dict[str, dict[str, Any]] | None = None,
) -> Celery:
    """构造 Celery app.

    Args:
        broker_url: broker 连接串 (eg "redis://:pwd@host:6379/1").
        backend_url: result backend 连接串. 摘要任务不取结果, 但 Celery 要求
            backend 字段非空 (除非显式 ignore_result), 写跟 broker 同一个就行.
        task_modules: autodiscover 的模块列表. None -> 默认扫描
            ["forge.memory.tasks", "forge.observability.cost.tasks"].
        beat_schedule: Celery beat schedule (周期任务). None -> 默认含
            observability.cost.flush (每 60s).

    Returns:
        Celery 实例.

    Beat 启动方式:
        - dev / 单机:   `celery -A ... worker -B`  (worker 进程内嵌 beat)
        - prod 推荐:    `celery -A ... worker` + `celery -A ... beat` 双进程
                       (多 worker 时只能其中一个带 -B, 否则 schedule 会重复触发)
    """
    try:
        from celery import Celery
    except ImportError as exc:
        raise RuntimeError(
            "celery 未安装. 请 `poetry install -E memory` 或把 celery 加进 main deps"
        ) from exc

    app = Celery("forge")
    app.conf.update(
        broker_url=broker_url,
        result_backend=backend_url,
        # 序列化 / 时区
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        # 行为
        task_acks_late=True,  # worker 崩了任务能被另一 worker 接走
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,  # 不预取, 摘要任务跑较慢, 公平分配
        broker_connection_retry_on_startup=True,
    )
    app.conf.beat_schedule = (
        beat_schedule
        if beat_schedule is not None
        else {
            "flush_llm_cost": {
                "task": "observability.cost.flush",
                "schedule": 60.0,
            },
        }
    )
    app.autodiscover_tasks(
        task_modules
        or [
            "forge.memory.tasks",
            "forge.observability.cost.tasks",
        ]
    )
    _register_worker_signals()
    return app


def _register_worker_signals() -> None:
    """worker 进程启动期一次性初始化 DB engine 等长寿对象.

    worker 不跑 FastAPI lifespan, 所以这里补上. init_engine 幂等,
    多次注册 / 多次调用都安全.
    """
    try:
        from celery.signals import worker_process_init
    except ImportError:  # pragma: no cover
        return

    @worker_process_init.connect
    def _init_worker(**kwargs):  # noqa: ARG001
        from forge.infrastructure.database.database import init_engine

        init_engine()
        logger.info("celery worker 进程: 数据库引擎已初始化")

        # 把 settings.llm.budget 同步到 worker 进程的 CostTracker.
        # worker 进程也会调 LLM (摘要等), 必须有同一份预算配置.
        try:
            from forge.config.settings import get_settings
            from forge.llm.cost_tracker import BudgetConfig, get_cost_tracker

            settings = get_settings()
            bcfg = settings.llm.budget
            get_cost_tracker().configure_budget(
                BudgetConfig(
                    user_daily_limits_usd=dict(bcfg.user_daily_limits_usd),
                    default_user_daily_limit_usd=bcfg.default_user_daily_limit_usd,
                    global_daily_limit_usd=bcfg.global_daily_limit_usd,
                    alert_threshold=bcfg.alert_threshold,
                )
            )
            logger.info("celery worker 进程: CostTracker budget 已加载")
        except Exception:  # noqa: BLE001
            logger.exception("celery worker 进程: CostTracker budget 加载失败 (预算检查 no-op)")


class CeleryTaskQueue(TaskQueue):
    """通过 Celery 派发任务的 TaskQueue 实现."""

    def __init__(self, app: Celery) -> None:
        self._app = app

    def submit(self, task_name: str, **kwargs: Any) -> None:
        # send_task 不需要业务方 import 装饰器; 任务名匹配 worker 端 @shared_task(name=...)
        self._app.send_task(task_name, kwargs=kwargs)
        logger.debug("CeleryTaskQueue 提交任务: task=%s kwargs=%s", task_name, kwargs)
