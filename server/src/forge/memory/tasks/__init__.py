"""Celery 任务定义集合.

Celery worker 启动时通过 autodiscover_tasks(["forge.memory.tasks"])
扫到本包下所有 @shared_task. 任务名通过 name 显式声明, 业务侧用
`queue.submit("memory.<name>", ...)` 寻址, 不 import 装饰器.
"""

from . import summarize  # noqa: F401  -- 触发任务注册

__all__ = ["summarize"]
