"""RunSupervisor — 进程内 AdaptiveRun 后台执行 + HITL 协调器.

设计目标 (P0-3 / HITL)：

- POST /runs 与 chat adaptive 入口建档后，由 supervisor 注册后台任务真正驱动
  orchestrator 跑完 7 步主流程。
- BLOCKED 状态等待人工 decide：``decide continue`` 触发 ``resume_run``，
  supervisor 把 run 状态推回 PLANNING 并重新唤起 orchestrator；
  ``decide abort`` / ``POST /runs/{id}/abort`` 触发 ``cancel_run``。
- supervisor 是进程内单例 + ``dict[str, asyncio.Task]`` 表；服务重启后
  in-flight run 丢失（按文档约定的"单机/单进程"假设；持久化恢复留给
  后续 worker 进程）。run 的状态、TaskGraph、options_snapshot 已通过
  AdaptiveRunStore 落盘，CLI / Web 可以从存储侧重新发现 run。

约束 (设计红线 §6 聊天路径零退化)：

- 仅作用于 adaptive 路径，不触碰 TurnOrchestrator。
- 不持有 LLM client / Planner 等可变状态，所有依赖由 caller 注入。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from forge.adaptive import events
from forge.adaptive.models import AdaptiveRun, RunStatus
from forge.adaptive.options import TaskOptions
from forge.adaptive.orchestrator import AdaptiveRunOrchestrator
from forge.adaptive.store import AdaptiveRunStore

logger = logging.getLogger(__name__)


# 由 caller 提供：根据 (run, options, store) 构造 orchestrator 实例
OrchestratorFactory = Callable[
    [AdaptiveRun, TaskOptions, AdaptiveRunStore],
    AdaptiveRunOrchestrator,
]


@dataclass
class _RunHandle:
    """单个后台 run 任务的句柄。"""

    run_id: str
    task: asyncio.Task
    store: AdaptiveRunStore


class RunSupervisor:
    """进程内 AdaptiveRun 调度单例。"""

    def __init__(self) -> None:
        self._handles: dict[str, _RunHandle] = {}
        self._lock = asyncio.Lock()

    async def start_run(
        self,
        *,
        run: AdaptiveRun,
        options: TaskOptions,
        store: AdaptiveRunStore,
        orchestrator_factory: OrchestratorFactory,
    ) -> asyncio.Task:
        """注册新 run 的后台执行任务。"""
        async with self._lock:
            if run.run_id in self._handles:
                existing = self._handles[run.run_id]
                if not existing.task.done():
                    logger.info("run 已在执行中 run_id=%s 跳过 start_run", run.run_id)
                    return existing.task
                # 已完成的旧 handle 清掉，重新调度（如恢复执行）
                self._handles.pop(run.run_id, None)

            orchestrator = orchestrator_factory(run, options, store)
            task = asyncio.create_task(
                self._run_lifecycle(run=run, options=options, store=store, orchestrator=orchestrator),
                name=f"adaptive-run-{run.run_id}",
            )
            self._handles[run.run_id] = _RunHandle(run_id=run.run_id, task=task, store=store)
            # C5/fix-13: 任务结束后异步清理 handle，避免依赖下次 start_run 才被回收
            def _cleanup_handle(_t: asyncio.Task, run_id: str = run.run_id) -> None:
                self._handles.pop(run_id, None)

            task.add_done_callback(_cleanup_handle)
            return task

    async def resume_run(
        self,
        *,
        run: AdaptiveRun,
        options: TaskOptions,
        store: AdaptiveRunStore,
        orchestrator_factory: OrchestratorFactory,
    ) -> asyncio.Task:
        """从 BLOCKED 状态恢复 run（HITL decide=continue）。"""
        if run.status != RunStatus.PLANNING:
            # decide 路由已把 BLOCKED -> PLANNING 状态推过来；其余状态拒绝恢复
            raise ValueError(
                f"resume_run 仅支持 run.status=PLANNING，当前: {run.status.value}"
            )
        logger.info("HITL 恢复执行 run_id=%s", run.run_id)
        return await self.start_run(
            run=run,
            options=options,
            store=store,
            orchestrator_factory=orchestrator_factory,
        )

    async def cancel_run(self, run_id: str) -> bool:
        """取消进行中的 run，返回 True 表示取消了在跑 task。"""
        async with self._lock:
            handle = self._handles.get(run_id)
        if handle is None or handle.task.done():
            return False
        handle.task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await handle.task
        return True

    def get_task(self, run_id: str) -> asyncio.Task | None:
        return (self._handles.get(run_id) or _RunHandle("", None, None)).task  # type: ignore[arg-type]

    def is_active(self, run_id: str) -> bool:
        handle = self._handles.get(run_id)
        return handle is not None and not handle.task.done()

    async def _run_lifecycle(
        self,
        *,
        run: AdaptiveRun,
        options: TaskOptions,
        store: AdaptiveRunStore,
        orchestrator: AdaptiveRunOrchestrator,
    ) -> AdaptiveRun:
        """orchestrator 执行包装：

        - cancel → ABORTED
        - 异常   → FAILED + RUN_FAILED 事件
        - 超时   → FAILED + reason=timeout (B12/P2-12)
        """
        timeout = options.hard_caps.max_run_duration_sec
        try:
            if timeout and timeout > 0:
                return await asyncio.wait_for(
                    orchestrator.run(run=run, options=options),
                    timeout=timeout,
                )
            return await orchestrator.run(run=run, options=options)
        except TimeoutError:
            logger.warning("run 超过最大执行时长 run_id=%s timeout=%.0fs", run.run_id, timeout)
            await self._safe_transition(
                store,
                run.run_id,
                RunStatus.FAILED,
                reason=f"max_run_duration_sec={timeout}",
                event_type=events.RUN_FAILED,
            )
            return run
        except asyncio.CancelledError:
            logger.info("run 被取消 run_id=%s", run.run_id)
            await self._safe_transition(store, run.run_id, RunStatus.ABORTED, reason="cancelled")
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("run 后台执行异常 run_id=%s", run.run_id)
            await self._safe_transition(
                store,
                run.run_id,
                RunStatus.FAILED,
                reason=str(exc),
                event_type=events.RUN_FAILED,
            )
            return run
        # 注：handle 在 start_run 中通过 task.add_done_callback 异步清理，
        # 这里 finally 不再操作 _handles 避免 task 尚未 done 时漏 pop。

    @staticmethod
    async def _safe_transition(
        store: AdaptiveRunStore,
        run_id: str,
        to_status: RunStatus,
        *,
        reason: str,
        event_type: str | None = None,
    ) -> None:
        try:
            await store.transition_status(run_id, to_status, payload={"reason": reason})
        except (ValueError, FileNotFoundError) as exc:
            logger.warning(
                "supervisor 状态收敛失败 run=%s -> %s err=%s",
                run_id,
                to_status.value,
                exc,
            )
        if event_type:
            try:
                await store.append_event(run_id, event_type, {"reason": reason})
            except Exception:  # noqa: BLE001
                logger.exception("supervisor 事件追加失败 run=%s event=%s", run_id, event_type)


_supervisor: RunSupervisor | None = None


def get_run_supervisor() -> RunSupervisor:
    """进程内单例访问入口。"""
    global _supervisor
    if _supervisor is None:
        _supervisor = RunSupervisor()
    return _supervisor


def reset_run_supervisor() -> None:
    """单测重置入口。"""
    global _supervisor
    _supervisor = None


def build_orchestrator_factory(
    *,
    planner_callable: Callable | None = None,
    discovery_callable: Callable | None = None,
    tool_allowlist: list[str] | None = None,
    event_handler: Callable[[dict], Awaitable[None] | None] | None = None,
    enable_real_llm: bool | None = None,
) -> OrchestratorFactory:
    """默认 orchestrator 工厂：构造 Planner / Validator / Executor / Integrator / Verifier 全套。

    ``enable_real_llm`` (B7/B8/P0-2):
      - True  -> 自动装配真实 DiscoveryAgent + PlannerLLM (要求 LLM pool 已就绪)
      - False -> 完全走 fallback，单测和零依赖环境可用
      - None  -> 读取 settings.task_execution.enable_real_llm，默认 False
    """
    from forge.adaptive.executor import TaskExecutor
    from forge.adaptive.integrator import Integrator
    from forge.adaptive.planner import Planner
    from forge.adaptive.validator import TaskGraphValidator
    from forge.adaptive.verifier import Verifier

    task_cfg = None
    try:
        from config.settings import get_settings

        settings = get_settings()
        task_cfg = getattr(settings, "task_execution", None)
    except Exception:  # noqa: BLE001
        task_cfg = None

    if enable_real_llm is None:
        enable_real_llm = bool(getattr(task_cfg, "enable_real_llm", False))

    if enable_real_llm and planner_callable is None:
        from forge.adaptive.planner_llm import build_real_planner_callable

        planner_profile = "smart"
        try:
            planner_profile = str(getattr(task_cfg, "planner_model_profile", planner_profile))
        except Exception:  # noqa: BLE001
            planner_profile = "smart"
        planner_callable = build_real_planner_callable(model_profile=planner_profile)
    if enable_real_llm and discovery_callable is None:
        from forge.adaptive.discovery import build_real_discovery_callable

        discovery_profile = "fast"
        try:
            discovery_profile = str(getattr(task_cfg, "discovery_model_profile", discovery_profile))
        except Exception:  # noqa: BLE001
            discovery_profile = "fast"
        discovery_callable = build_real_discovery_callable(model_profile=discovery_profile)

    def _factory(run: AdaptiveRun, options: TaskOptions, store: AdaptiveRunStore) -> AdaptiveRunOrchestrator:
        _ = run, options  # 未来 supervisor 可基于 run/options 选择不同实现
        planner = Planner(plan_func=planner_callable)
        validator = TaskGraphValidator(tool_allowlist=list(tool_allowlist or []))
        executor = TaskExecutor(
            store=store,
            event_handler=event_handler,
            enable_real_llm=bool(enable_real_llm),
        )
        integrator = Integrator(store=store)
        verifier = Verifier()
        return AdaptiveRunOrchestrator(
            planner=planner,
            validator=validator,
            executor=executor,
            integrator=integrator,
            verifier=verifier,
            store=store,
            tool_allowlist=list(tool_allowlist or []),
            event_handler=event_handler,
            discovery_callable=discovery_callable,
        )

    return _factory
