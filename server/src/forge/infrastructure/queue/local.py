"""本地任务队列实现: 同进程异步执行."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from typing import Any

from forge.config.domains.paths import tasks_db_path
from forge.infrastructure.queue.base import TaskQueue
from forge.infrastructure.queue.handlers import resolve_task_handler
from forge.infrastructure.queue.local_store import LocalQueuedTask, LocalTaskStore

logger = logging.getLogger(__name__)


class LocalTaskQueue(TaskQueue):
    """单机模式默认任务队列.

    - 若当前在事件循环中: `create_task` 后台执行 (fire-and-forget)
    - 若当前不在事件循环中: `asyncio.run` 同步执行
    """

    def __init__(
        self,
        *,
        handler_resolver: Callable[[str], Callable[..., Any] | None] = resolve_task_handler,
        max_attempts: int = 3,
        retry_delay_seconds: float = 30.0,
        store: LocalTaskStore | None = None,
    ) -> None:
        self._resolve = handler_resolver
        self._max_attempts = max(1, int(max_attempts))
        self._retry_delay_seconds = max(0.0, float(retry_delay_seconds))
        self._store = store or LocalTaskStore(tasks_db_path())
        self._bootstrapped = False
        self._bootstrap_lock = asyncio.Lock()
        self._drain_lock = asyncio.Lock()
        self._drain_requested = False
        self._wakeup_task: asyncio.Task[None] | None = None
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._bootstrap_and_drain())
        except RuntimeError:
            # 非异步上下文初始化, 等首次 submit 再补 bootstrap
            pass

    def submit(self, task_name: str, **kwargs: Any) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._enqueue(task_name, kwargs))
        except RuntimeError:
            asyncio.run(self._enqueue(task_name, kwargs))

    async def _bootstrap_and_drain(self) -> None:
        await self._ensure_bootstrapped()
        await self._drain_pending()

    async def _ensure_bootstrapped(self) -> None:
        if self._bootstrapped:
            return
        async with self._bootstrap_lock:
            if self._bootstrapped:
                return
            await self._store.init()
            recovered = await self._store.recover_running()
            if recovered > 0:
                logger.warning("LocalTaskQueue: 已恢复 %d 条 running 任务为 pending", recovered)
            self._bootstrapped = True

    async def _enqueue(self, task_name: str, kwargs: dict[str, Any]) -> None:
        if self._resolve(task_name) is None:
            logger.warning("LocalTaskQueue: 未注册任务 task=%s kwargs=%s", task_name, kwargs)
            return
        await self._ensure_bootstrapped()
        task_id = await self._store.enqueue(
            task_name,
            kwargs,
            max_attempts=self._max_attempts,
        )
        logger.debug("LocalTaskQueue 入队 task_id=%s task=%s", task_id, task_name)
        await self._drain_pending()

    async def _drain_pending(self) -> None:
        self._drain_requested = True
        if self._drain_lock.locked():
            return
        async with self._drain_lock:
            while self._drain_requested:
                self._drain_requested = False
                while True:
                    task = await self._store.claim_due()
                    if task is None:
                        break
                    await self._run_one(task)
        await self._schedule_next_wakeup()

    async def _run_one(self, task: LocalQueuedTask) -> None:
        handler = self._resolve(task.task_name)
        if handler is None:
            await self._store.mark_dead(
                task.id,
                attempts=task.attempts + 1,
                error=f"handler not found for task={task.task_name}",
            )
            logger.error(
                "LocalTaskQueue 任务失败(无处理器) task_id=%s task=%s", task.id, task.task_name
            )
            return
        try:
            result = handler(**task.payload)
            if inspect.isawaitable(result):
                await result
            await self._store.mark_succeeded(task.id)
            logger.debug("LocalTaskQueue 任务完成 task_id=%s task=%s", task.id, task.task_name)
        except Exception as exc:  # noqa: BLE001
            attempts = task.attempts + 1
            err = repr(exc)
            if attempts >= task.max_attempts:
                await self._store.mark_dead(task.id, attempts=attempts, error=err)
                logger.exception(
                    "LocalTaskQueue 任务失败并进入dead task_id=%s task=%s attempts=%s",
                    task.id,
                    task.task_name,
                    attempts,
                )
                return
            await self._store.mark_retry(
                task.id,
                attempts=attempts,
                error=err,
                retry_delay_seconds=self._retry_delay_seconds,
            )
            logger.exception(
                "LocalTaskQueue 任务失败,等待重试 task_id=%s task=%s attempt=%s/%s",
                task.id,
                task.task_name,
                attempts,
                task.max_attempts,
            )

    async def _schedule_next_wakeup(self) -> None:
        delay = await self._store.next_due_delay()
        if delay is None:
            return
        if self._wakeup_task and not self._wakeup_task.done():
            return
        self._wakeup_task = asyncio.create_task(self._wakeup_after(delay))

    async def _wakeup_after(self, delay: float) -> None:
        await asyncio.sleep(delay)
        await self._drain_pending()
