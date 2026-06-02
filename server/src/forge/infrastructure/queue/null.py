"""空实现: dev / 单测 / 未装 Celery 时的兜底.

submit 只记日志, 不真派发. 让没有 worker 的环境 ContextBuilder 等链路照常跑.
"""

from __future__ import annotations

import logging
from typing import Any

from forge.infrastructure.queue.base import TaskQueue

logger = logging.getLogger(__name__)


class NullTaskQueue(TaskQueue):
    """TaskQueue ABC 的空实现. 任何 submit 只记 INFO."""

    def submit(self, task_name: str, **kwargs: Any) -> None:
        logger.info("NullTaskQueue.submit (无 worker): task=%s kwargs=%s", task_name, kwargs)
