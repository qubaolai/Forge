"""RunSupervisor: 进程内 RunOrchestrator 实例追踪.

仅 CLI 模式 (plan_exec / workflow) 用. chat 模式不需要 supervisor (turn 短生命).

职责:
    - register(orchestrator) 放进 active_runs, 启动后台 task
    - get(run_id) 路由 /runs/{id}/abort 查找用
    - on task done 自动 unregister
    - shutdown() lifespan 关闭时取消所有未完成 task

实现: 单进程 in-memory 字典 + asyncio.Lock; 多进程后接 Redis pub/sub 平替.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import suppress

from forge.agents.run_orchestrator import RunOrchestrator

logger = logging.getLogger(__name__)


class RunSupervisor:
    """全局 RunOrchestrator 索引."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[str, RunOrchestrator] = {}

    def register(self, orchestrator: RunOrchestrator) -> asyncio.Task:
        """放入索引并启动 task. 同 run_id 重复注册返回已存在 task."""
        with self._lock:
            existing = self._active.get(orchestrator.run_id)
            if existing is not None:
                existing_task = existing.task
                if existing_task is not None and not existing_task.done():
                    logger.warning(
                        "run %s 已在 supervisor 中, 跳过重复注册", orchestrator.run_id,
                    )
                    return existing_task
            self._active[orchestrator.run_id] = orchestrator

        task = orchestrator.schedule()
        run_id = orchestrator.run_id

        def _remove_finished(_task: asyncio.Task) -> None:
            self._on_done(run_id)

        task.add_done_callback(_remove_finished)
        return task

    def get(self, run_id: str) -> RunOrchestrator | None:
        with self._lock:
            return self._active.get(run_id)

    async def cancel(self, run_id: str) -> bool:
        """中止 run. 返回是否成功触发 (run 不存在/已结束返回 False)."""
        orch = self.get(run_id)
        if orch is None:
            return False
        orch.abort()
        task = orch.task
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        return True

    async def shutdown(self) -> None:
        """lifespan 关闭. 取消所有未完成 task."""
        with self._lock:
            active = list(self._active.values())
        for orch in active:
            orch.abort()
            task = orch.task
            if task is not None and not task.done():
                task.cancel()
        # 等所有 task 收尾
        for orch in active:
            task = orch.task
            if task is None:
                continue
            with suppress(asyncio.CancelledError, Exception):
                await task
        with self._lock:
            self._active.clear()

    def _on_done(self, run_id: str) -> None:
        with self._lock:
            self._active.pop(run_id, None)


# ---------------------------------------------------------------------------
# 单例
# ---------------------------------------------------------------------------
_supervisor: RunSupervisor | None = None
_singleton_lock = threading.Lock()


def get_run_supervisor() -> RunSupervisor:
    global _supervisor
    if _supervisor is None:
        with _singleton_lock:
            if _supervisor is None:
                _supervisor = RunSupervisor()
    return _supervisor


def reset_run_supervisor() -> None:
    """测试隔离用."""
    global _supervisor
    with _singleton_lock:
        _supervisor = None


__all__ = [
    "RunSupervisor",
    "get_run_supervisor",
    "reset_run_supervisor",
]
