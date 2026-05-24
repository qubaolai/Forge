"""Celery worker 进程的入口模块.

启动 worker:
    celery -A forge.infrastructure.queue.celery_app worker -l info

celery 命令会 import 本模块, 拿模块级 ``app`` 符号; 所以本模块在 import 时就
要把 app 装配好 (读 settings -> build_celery_app).

跟 web 进程的关系:
    web 进程: api/lifespan.py 调 init_task_queue() -> 内部走 build_celery_app
    worker:   直接 import 本模块 -> 触发同样的 build_celery_app

两边都用同一份 settings, 同一份 task_modules, 因此 broker / 任务注册一致.
"""

from __future__ import annotations

from forge.config.settings import get_settings

from forge.infrastructure.queue.celery_queue import build_celery_app

_settings = get_settings()

app = build_celery_app(
    broker_url=_settings.celery.broker_url or _settings.redis.url,
    backend_url=(
        _settings.celery.backend_url or _settings.celery.broker_url or _settings.redis.url
    ),
    task_modules=_settings.celery.task_modules,
)
"""模块级 Celery app. `celery -A ...:app` 直接拿."""
