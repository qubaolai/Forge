"""任务队列: Protocol + 实现 + 全局单例工厂.

单机模式默认 `LocalTaskQueue` (同进程异步执行).
如需兼容旧 Celery worker, 可设置 `ASSISTANT_TASK_QUEUE_BACKEND=celery`.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from forge.infrastructure.queue.base import TaskQueue
from forge.infrastructure.queue.local import LocalTaskQueue
from forge.infrastructure.queue.null import NullTaskQueue

logger = logging.getLogger(__name__)

_DEFAULT_QUEUE: TaskQueue | None = None
_CELERY_APP: Any | None = None  # Celery 类型 lazy import


def init_task_queue(
    *,
    enabled: bool,
    broker_url: str | None,
    backend_url: str | None,
    task_modules: list[str] | None = None,
) -> TaskQueue:
    """lifespan 启动期调用. 幂等.

    - enabled=False -> NullTaskQueue
    - enabled=True 且 backend=local(默认) -> LocalTaskQueue
    - enabled=True 且 backend=celery -> 旧 Celery 路径
    """
    global _DEFAULT_QUEUE, _CELERY_APP
    if _DEFAULT_QUEUE is not None:
        return _DEFAULT_QUEUE

    if not enabled:
        logger.info("TaskQueue: 未启用 (enabled=%s) -> NullTaskQueue", enabled)
        _DEFAULT_QUEUE = NullTaskQueue()
        return _DEFAULT_QUEUE

    backend = os.environ.get("ASSISTANT_TASK_QUEUE_BACKEND", "local").strip().lower()
    if backend != "celery":
        _DEFAULT_QUEUE = LocalTaskQueue()
        logger.info("TaskQueue 就绪: LocalTaskQueue")
        return _DEFAULT_QUEUE

    if not broker_url:
        logger.info("TaskQueue: backend=celery 但 broker_url 为空 -> NullTaskQueue")
        _DEFAULT_QUEUE = NullTaskQueue()
        return _DEFAULT_QUEUE

    try:
        from forge.infrastructure.queue.celery_queue import (
            CeleryTaskQueue,
            build_celery_app,
        )

        _CELERY_APP = build_celery_app(
            broker_url=broker_url,
            backend_url=backend_url or broker_url,
            task_modules=task_modules,
        )
        _DEFAULT_QUEUE = CeleryTaskQueue(_CELERY_APP)
        logger.info("TaskQueue 就绪: Celery (broker=%s)", _safe_url(broker_url))
    except Exception as exc:  # noqa: BLE001
        logger.warning("TaskQueue: Celery 初始化失败, 降级到 NullTaskQueue: %s", exc)
        _DEFAULT_QUEUE = NullTaskQueue()
    return _DEFAULT_QUEUE


def get_task_queue() -> TaskQueue:
    """业务侧入口. lifespan 未跑过会拿到 NullTaskQueue (而不是崩) -- 单测 / 脚本友好."""
    global _DEFAULT_QUEUE
    if _DEFAULT_QUEUE is None:
        _DEFAULT_QUEUE = NullTaskQueue()
    return _DEFAULT_QUEUE


def reset_task_queue() -> None:
    """单测用."""
    global _DEFAULT_QUEUE, _CELERY_APP
    _DEFAULT_QUEUE = None
    _CELERY_APP = None


def _safe_url(url: str) -> str:
    """脱敏 URL 里的密码. redis://user:pwd@host -> redis://user:***@host"""
    if "@" not in url:
        return url
    head, tail = url.split("@", 1)
    if ":" not in head:
        return url
    proto_user, _ = head.rsplit(":", 1)
    return f"{proto_user}:***@{tail}"


__all__ = [
    "TaskQueue",
    "NullTaskQueue",
    "LocalTaskQueue",
    "init_task_queue",
    "get_task_queue",
    "reset_task_queue",
]
