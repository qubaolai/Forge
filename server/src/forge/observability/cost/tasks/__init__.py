"""Cost-related Celery 任务.

worker autodiscover 走 task_modules = [..., "forge.observability.cost.tasks"].
"""

from . import flush_cost  # noqa: F401  -- 触发 @shared_task 注册

__all__ = ["flush_cost"]
