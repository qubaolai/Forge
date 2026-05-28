"""RunOrchestrator: CLI 模式 (plan_exec / workflow) 的顶层编排.

与 TurnOrchestrator (chat) 平级, 但持久化走 RunStore (JSONL), 而非 chat_messages DB.

职责:
    1. POST /v1/runs 创建一个 run, 调 RunStore.create_run 落档案
    2. 后台 task 跑 ReActAgent.stream, 经 Lifecycle 落 events.jsonl / artifacts
    3. SSE 客户端通过 RunStore.list_events cursor 模式回放
    4. POST /v1/decisions/{token} (Plan / workflow_gate) 在 HITL 唤醒等待方

约束:
    - 单 RunOrchestrator 仅服务一个 run (一次性对象, 不池化)
    - run_in_background 通过 asyncio.create_task; supervisor 在路由层托管
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from forge.agents.lifecycle import AgentLifecycle, RunContext
from forge.agents.persistence_lifecycle import RunStorePersistenceLifecycle
from forge.agents.plan_mode import PlanModeLifecycle
from forge.agents.react.agent import ReActAgent
from forge.agents.workflow_lifecycle import WorkflowLifecycle
from forge.chat.guards import (
    GuardLifecycleAdapter,
    LoopGuard,
    StepSafetyNet,
    StuckDetector,
    TokenBudgetGuard,
    WallClockGuard,
)
from forge.config.domains.agent_profiles import AgentProfile
from forge.config.settings import get_settings
from forge.infrastructure.run_store import RunStore
from forge.infrastructure.run_store_models import RunRecord
from forge.llm import GatewayBinding, GatewayLLMAdapter, get_llm_gateway
from forge.tools.registry import ToolRegistry
from forge.workspace.runtime import resolve_runtime_settings

logger = logging.getLogger(__name__)


class RunOrchestrator:
    """单个 run 的执行器. 通过 schedule_run() 启动后台 task."""

    def __init__(
        self,
        *,
        record: RunRecord,
        store: RunStore,
        profile: AgentProfile,
        goal: str,
        user_id: str | None,
        user_system_prompt: str = "",
        model_options: dict[str, Any] | None = None,
        workflow_template: dict[str, Any] | None = None,
    ) -> None:
        self._record = record
        self._store = store
        self._profile = profile
        self._goal = goal
        self._user_id = user_id
        self._user_system_prompt = user_system_prompt
        self._model_options = model_options or {}
        self._workflow_template = workflow_template
        self._abort_event = asyncio.Event()
        self._task: asyncio.Task | None = None

    @property
    def run_id(self) -> str:
        return self._record.run_id

    @property
    def record(self) -> RunRecord:
        return self._record

    @property
    def task(self) -> asyncio.Task | None:
        return self._task

    def abort(self) -> None:
        """主动中止 (路由 /runs/{id}/abort 用)."""
        self._abort_event.set()

    def schedule(self) -> asyncio.Task:
        """后台跑 _execute(); 调用方负责追踪 task."""
        if self._task is not None:
            return self._task
        self._task = asyncio.create_task(self._execute(), name=f"run:{self.run_id}")
        return self._task

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------
    async def _execute(self) -> None:
        """主执行体. 仅日志副作用 (事件流通过 lifecycle 写)."""
        started_at = time.perf_counter()
        try:
            llm = self._build_llm()
            system_prompt = self._render_system_prompt()
            lifecycles = self._build_lifecycles()
            agent = ReActAgent(
                llm,
                system_prompt=system_prompt,
                max_steps=self._profile.max_steps,
                tools=self._resolve_tools(),
            )
            run_ctx = RunContext(
                run_id=self.run_id,
                mode=self._record.mode,
                user_id=self._user_id,
                metadata={"workspace_path": self._record.workspace_path},
            )

            from forge.agents.lifecycle import MultiLifecycle
            lifecycle = MultiLifecycle(lifecycles)

            # CLI 主用户消息 = goal (没有多轮 history)
            event_count = 0
            async for event in agent.stream(
                self._goal,
                history=None,
                abort_event=self._abort_event,
                model_options=self._model_options,
                lifecycle=lifecycle,
                run_ctx=run_ctx,
            ):
                event_count += 1
                # 主 agent 的细颗粒度事件走 chat 事件协议;
                # 这里只把"端到端可见"的事件落到 events.jsonl, 其余在 lifecycle hooks 内已写.
                if event.type in ("done", "error"):
                    await self._store.append_event(
                        self.run_id,
                        f"agent_{event.type}",
                        event.to_dict(),
                    )

            logger.info(
                "Run %s 执行结束: events=%d duration_ms=%.1f",
                self.run_id, event_count, (time.perf_counter() - started_at) * 1000,
            )
        except asyncio.CancelledError:
            logger.info("Run %s 被取消", self.run_id)
            try:
                await self._store.transition_status(
                    self.run_id, "aborted",
                    payload={"reason": "task_cancelled"},
                )
            except Exception:  # noqa: BLE001
                logger.exception("取消时 transition_status 失败 run=%s", self.run_id)
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Run %s 执行异常", self.run_id)
            try:
                await self._store.transition_status(
                    self.run_id, "failed",
                    payload={"error": str(exc), "error_type": type(exc).__name__},
                )
            except Exception:  # noqa: BLE001
                logger.exception("异常时 transition_status 失败 run=%s", self.run_id)

    # ------------------------------------------------------------------
    # builders
    # ------------------------------------------------------------------
    def _build_llm(self) -> GatewayLLMAdapter:
        settings = get_settings()
        binding = GatewayBinding(
            gateway=get_llm_gateway(settings),
            user_id=self._user_id,
            preferred_provider=self._model_options.get("provider"),
            preferred_model=self._model_options.get("model"),
            model_profile=self._profile.model_profile,
            task_type="tool_use",
        )
        return GatewayLLMAdapter(binding)

    def _render_system_prompt(self) -> str:
        from forge.prompts import get_registry
        return get_registry().render(
            self._profile.system_prompt_template,
            user_system_prompt=self._user_system_prompt,
            workspace_path=self._record.workspace_path,
            goal=self._goal,
        )

    def _resolve_tools(self) -> list:
        tools = [
            t for t in ToolRegistry.get_all() if t.name in set(self._profile.tools_allowed)
        ]
        return tools

    def _build_lifecycles(self) -> list[AgentLifecycle]:
        runtime = resolve_runtime_settings()
        max_steps = self._profile.max_steps
        guards: list[LoopGuard] = [
            StepSafetyNet(),
            StuckDetector(),
            TokenBudgetGuard(),
            WallClockGuard(
                soft_limit_sec=runtime.wall_clock_soft_limit_sec,
                warn_limit_sec=runtime.wall_clock_warn_limit_sec,
                hard_limit_sec=runtime.wall_clock_hard_limit_sec,
            ),
        ]
        _ = max_steps  # 保留以备未来 guards 用
        lifecycles: list[AgentLifecycle] = [GuardLifecycleAdapter(guards)]

        # Plan Mode (plan_exec 模式)
        if self._profile.plan_mode_initial:
            readonly_schemas, full_schemas = self._partition_schemas()
            lifecycles.append(
                PlanModeLifecycle(
                    readonly_schemas=readonly_schemas,
                    full_schemas=full_schemas,
                    run_id=self.run_id,
                    owner_user_id=self._user_id,
                    store=self._store,
                )
            )

        # Workflow (requires_template 模式)
        if self._profile.requires_template and self._workflow_template is not None:
            lifecycles.append(
                WorkflowLifecycle(
                    self._workflow_template,
                    run_id=self.run_id,
                    owner_user_id=self._user_id,
                    store=self._store,
                )
            )

        # 持久化 (run_store 模式)
        if self._profile.persistence == "run_store":
            lifecycles.append(
                RunStorePersistenceLifecycle(
                    self._store,
                    self.run_id,
                    large_artifact_threshold_bytes=(
                        self._profile.large_artifact_threshold_bytes
                    ),
                )
            )

        return lifecycles

    def _partition_schemas(self) -> tuple[list[dict], list[dict]]:
        """按 readonly_tools / tools_allowed 切两份 schema.

        Plan 阶段: readonly_tools 子集
        Exec 阶段: tools_allowed - {exit_plan_mode} (Plan 已退出, exit_plan_mode 不再暴露)
        """
        all_tools = ToolRegistry.get_all()
        by_name = {t.name: t for t in all_tools}
        readonly_set = set(self._profile.readonly_tools)
        full_set = set(self._profile.tools_allowed) - {"exit_plan_mode"}
        readonly_schemas = [
            by_name[n].openai_schema() for n in self._profile.readonly_tools if n in by_name
        ]
        full_schemas = [
            by_name[n].openai_schema() for n in full_set if n in by_name
        ]
        _ = readonly_set  # noqa
        return readonly_schemas, full_schemas


__all__ = ["RunOrchestrator"]
