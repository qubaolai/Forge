"""AdaptiveRun 主流程编排（M7：discover→plan→validate→execute→integrate→close）。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from forge.adaptive import events
from forge.adaptive.executor import ExecuteSummary, TaskExecutor
from forge.adaptive.integrator import IntegrationResult, Integrator
from forge.adaptive.models import (
    AdaptiveRun,
    Artifact,
    ArtifactKind,
    RunStatus,
    TaskGraph,
    TaskKind,
    TaskNode,
)
from forge.adaptive.options import TaskOptions
from forge.adaptive.planner import Planner
from forge.adaptive.store import AdaptiveRunStore
from forge.adaptive.validator import TaskGraphValidationError, TaskGraphValidator
from forge.adaptive.verifier import Verifier, VerifyResult
from forge.utils.id_generator import new_id

EventHandler = Callable[[dict[str, Any]], Awaitable[None] | None]

@dataclass(frozen=True)
class PlanValidateOutcome:
    """M4 阶段规划与校验结果。"""

    task_graph: TaskGraph
    attempts: int


class AdaptiveRunOrchestrator:
    """AdaptiveRun 编排器。"""

    def __init__(
        self,
        *,
        planner: Planner,
        validator: TaskGraphValidator,
        executor: TaskExecutor | None = None,
        integrator: Integrator | None = None,
        verifier: Verifier | None = None,
        store: AdaptiveRunStore | None = None,
        tool_allowlist: list[str] | None = None,
        event_handler: EventHandler | None = None,
        discovery_callable: Callable[[str, str], Awaitable[str]] | None = None,
    ) -> None:
        self._planner = planner
        self._validator = validator
        self._executor = executor or TaskExecutor(store=store)
        self._integrator = integrator or Integrator(store=store)
        self._verifier = verifier or Verifier()
        self._store = store
        self._tool_allowlist = list(tool_allowlist or [])
        self._event_handler = event_handler
        # B7/P0-2 上半：可选真实 DiscoveryAgent；未注入时走轻量占位
        self._discovery_callable = discovery_callable

    async def plan_and_validate(
        self,
        *,
        run: AdaptiveRun,
        options: TaskOptions,
        discovery_report: str = "",
    ) -> PlanValidateOutcome:
        """执行 PLAN/VALIDATE，并按上限重试 replan。"""
        max_replans = options.hard_caps.max_replans
        attempts = 0
        last_error: TaskGraphValidationError | None = None

        while attempts <= max_replans:
            attempts += 1
            await self._set_status(run, RunStatus.PLANNING)
            await self._emit(
                run.run_id,
                events.PLAN_CREATED,
                {"attempt": attempts, "replan_count": run.replan_count},
            )

            task_graph = await self._planner.plan(
                goal=run.goal,
                discovery_report=discovery_report,
                tool_allowlist=self._tool_allowlist,
                options=options,
            )
            run.task_graph = task_graph
            await self._persist(run)

            # B1/P2-10: 每次 plan 后落盘 TaskGraph artifact，附 attempt 号方便排查
            await self._save_task_graph_artifact(
                run=run,
                task_graph=task_graph,
                attempt=attempts,
                status="planned",
            )

            await self._set_status(run, RunStatus.VALIDATING)
            try:
                self._validator.assert_valid(task_graph, options=options)
            except TaskGraphValidationError as exc:
                last_error = exc
                # B1: 校验失败时也落一份，包含 issues 便于回放
                await self._save_task_graph_artifact(
                    run=run,
                    task_graph=task_graph,
                    attempt=attempts,
                    status="rejected",
                    issues=[
                        {"code": issue.code, "message": issue.message, "node_id": issue.node_id}
                        for issue in exc.issues
                    ],
                )
                await self._emit(
                    run.run_id,
                    events.PLAN_REJECTED,
                    {
                        "attempt": attempts,
                        "issues": [
                            {"code": issue.code, "message": issue.message, "node_id": issue.node_id}
                            for issue in exc.issues
                        ],
                    },
                )
                if attempts > max_replans:
                    await self._set_status(run, RunStatus.BLOCKED)
                    await self._emit(
                        run.run_id,
                        events.RUN_BLOCKED,
                        {"reason": "replan_exhausted", "attempts": attempts},
                    )
                    raise
                run.replan_count += 1
                await self._persist(run)
                await self._set_status(run, RunStatus.PLANNING)
                continue

            await self._emit(run.run_id, events.PLAN_VALIDATED, {"attempt": attempts})
            return PlanValidateOutcome(task_graph=task_graph, attempts=attempts)

        # 理论上不会到达，这里作为静态兜底。
        if last_error is not None:
            raise last_error
        raise RuntimeError("plan_and_validate 未产出结果")

    async def run(self, *, run: AdaptiveRun, options: TaskOptions) -> AdaptiveRun:
        """执行 M7 全流程。支持 HITL decide continue 后从 PLANNING 状态恢复。

        恢复策略：当 run 已经有 task_graph 且 status=PLANNING（典型场景是
        BLOCKED → decide continue），跳过 DISCOVER，直接进入 plan_and_validate
        + execute；replan 计数沿用，避免无限重试。
        """
        is_resume = run.task_graph is not None and run.status == RunStatus.PLANNING
        await self._persist(run)
        if is_resume:
            await self._emit(
                run.run_id,
                events.RUN_STATUS_CHANGED,
                {
                    "from_status": RunStatus.PLANNING.value,
                    "to_status": RunStatus.PLANNING.value,
                    "reason": "resume_from_blocked",
                    "replan_count": run.replan_count,
                },
            )
            discovery_report = ""
            # 恢复时复用历史 discovery_report，从 run.metadata 兜底
            discovery_report = str(run.metadata.get("discovery_summary", ""))
        else:
            await self._emit(
                run.run_id,
                events.RUN_CREATED,
                {"status": run.status.value, "goal": run.goal, "owner_user_id": run.owner_user_id},
            )
            discovery_artifact = await self._discover(run=run, options=options)
            discovery_report = str(discovery_artifact.payload.get("summary", ""))
            # 记入 metadata 便于恢复阶段重用，避免重复发现
            run.metadata["discovery_summary"] = discovery_report
            await self._persist(run)

        try:
            await self.plan_and_validate(run=run, options=options, discovery_report=discovery_report)
        except TaskGraphValidationError:
            return run

        summary = await self._executor.execute(run=run, options=options)
        if summary.failed > 0:
            await self._set_status(run, RunStatus.FAILED)
            await self._emit(
                run.run_id,
                events.RUN_FAILED,
                {
                    "completed": summary.completed,
                    "failed": summary.failed,
                    "skipped": summary.skipped,
                },
            )
            return run

        integration = await self._integrate(run=run)
        if integration.conflict:
            await self._set_status(run, RunStatus.BLOCKED)
            await self._emit(
                run.run_id,
                events.RUN_BLOCKED,
                {
                    "reason": integration.reason,
                    "integration_report_id": integration.report_artifact.artifact_id,
                    "conflict_report_id": (
                        integration.conflict_artifact.artifact_id
                        if integration.conflict_artifact
                        else None
                    ),
                },
            )
            return run

        verify = await self._verify_and_repair(run=run, options=options)
        if verify is None:
            return run

        await self._close(run=run, summary=summary, integration=integration, verify=verify)
        return run

    async def _save_task_graph_artifact(
        self,
        *,
        run: AdaptiveRun,
        task_graph: TaskGraph,
        attempt: int,
        status: str,
        issues: list[dict] | None = None,
    ) -> Artifact | None:
        """B1/P2-10: 落盘 task_graph artifact 便于回放与排查。"""
        artifact = Artifact(
            artifact_id=new_id("art"),
            run_id=run.run_id,
            task_id=None,
            kind=ArtifactKind.TASK_GRAPH,
            payload={
                "attempt": attempt,
                "status": status,  # "planned" / "rejected"
                "replan_count": run.replan_count,
                "task_graph": task_graph.to_dict(),
                "issues": issues or [],
            },
        )
        run.artifact_ids.append(artifact.artifact_id)
        if self._store is not None:
            await self._store.save_artifact(artifact)
            await self._persist(run)
        await self._emit(
            run.run_id,
            events.ARTIFACT_CREATED,
            {
                "artifact_id": artifact.artifact_id,
                "kind": artifact.kind.value,
                "attempt": attempt,
                "status": status,
            },
        )
        return artifact

    async def _discover(self, *, run: AdaptiveRun, options: TaskOptions) -> Artifact:
        """B7/P0-2 上半：可注入真实 DiscoveryAgent。

        - 已注入 ``discovery_callable``：调用其产出 summary，失败回退占位文本。
        - 未注入：返回轻量占位（保留旧行为，单测和默认体验零依赖）。
        """
        summary = f"已完成快速探索: goal={run.goal[:80]}"
        source = "fallback"
        if self._discovery_callable is not None:
            try:
                real_summary = await self._discovery_callable(run.goal, options.workspace_path)
                if real_summary:
                    summary = real_summary
                    source = "react_agent"
            except Exception:
                import logging
                logging.getLogger(__name__).exception(
                    "DiscoveryAgent 执行失败，回退占位 summary run_id=%s", run.run_id
                )

        artifact = Artifact(
            artifact_id=new_id("art"),
            run_id=run.run_id,
            task_id=None,
            kind=ArtifactKind.DISCOVERY_REPORT,
            payload={
                "summary": summary,
                "workspace_path": options.workspace_path,
                "source": source,
                "created_at": datetime.now(UTC).isoformat(),
            },
        )
        run.artifact_ids.append(artifact.artifact_id)
        if self._store is not None:
            await self._store.save_artifact(artifact)
            await self._persist(run)
        await self._emit(
            run.run_id,
            events.ARTIFACT_CREATED,
            {"artifact_id": artifact.artifact_id, "kind": artifact.kind.value},
        )
        return artifact

    async def _integrate(
        self,
        *,
        run: AdaptiveRun,
        attempt: int | None = None,
    ) -> IntegrationResult:
        await self._set_status(run, RunStatus.INTEGRATING)
        await self._emit(
            run.run_id,
            events.INTEGRATION_STARTED,
            {"artifact_ids": list(run.artifact_ids), "attempt": attempt},
        )
        result = await self._integrator.merge(run=run, attempt=attempt)
        if result.conflict:
            await self._emit(
                run.run_id,
                events.INTEGRATION_CONFLICT,
                {
                    "reason": result.reason,
                    "report_artifact_id": result.report_artifact.artifact_id,
                    "conflict_artifact_id": (
                        result.conflict_artifact.artifact_id if result.conflict_artifact else None
                    ),
                },
            )
            await self._persist(run)
            return result
        await self._emit(
            run.run_id,
            events.INTEGRATION_COMPLETED,
            {
                "reason": result.reason,
                "report_artifact_id": result.report_artifact.artifact_id,
            },
        )
        await self._persist(run)
        return result

    async def _close(
        self,
        *,
        run: AdaptiveRun,
        summary: ExecuteSummary,
        integration: IntegrationResult,
        verify: VerifyResult,
    ) -> None:
        final_artifact = Artifact(
            artifact_id=new_id("art"),
            run_id=run.run_id,
            task_id=None,
            kind=ArtifactKind.FINAL_REPORT,
            payload={
                "run_id": run.run_id,
                "status": "completed",
                "task_graph": run.task_graph.to_dict() if run.task_graph else None,
                "artifact_ids": list(run.artifact_ids),
                "execution_summary": {
                    "completed": summary.completed,
                    "failed": summary.failed,
                    "skipped": summary.skipped,
                },
                "integration_summary": {
                    "merged": integration.merged,
                    "reason": integration.reason,
                    "integration_report_id": integration.report_artifact.artifact_id,
                    "conflict_report_id": (
                        integration.conflict_artifact.artifact_id
                        if integration.conflict_artifact
                        else None
                    ),
                },
                "verify_summary": {
                    "passed": verify.passed,
                    "command": verify.command,
                    "returncode": verify.returncode,
                    "duration_ms": verify.duration_ms,
                    "timed_out": verify.timed_out,
                    "skipped": verify.skipped,
                },
            },
        )
        run.artifact_ids.append(final_artifact.artifact_id)
        if self._store is not None:
            await self._store.save_artifact(final_artifact)
        await self._emit(
            run.run_id,
            events.ARTIFACT_CREATED,
            {"artifact_id": final_artifact.artifact_id, "kind": final_artifact.kind.value},
        )
        await self._set_status(run, RunStatus.COMPLETED)
        await self._emit(
            run.run_id,
            events.RUN_COMPLETED,
            {"status": RunStatus.COMPLETED.value, "artifact_id": final_artifact.artifact_id},
        )

    async def _verify_and_repair(self, *, run: AdaptiveRun, options: TaskOptions) -> VerifyResult | None:
        """执行验证；失败时生成修复任务并循环。"""
        max_replans = options.hard_caps.max_replans

        while True:
            await self._set_status(run, RunStatus.VERIFYING)
            await self._emit(run.run_id, events.VERIFY_STARTED, {"command": options.verifier_cmd})
            verify = await self._verifier.run(options.verifier_cmd, workspace_path=options.workspace_path)

            test_art = Artifact(
                artifact_id=new_id("art"),
                run_id=run.run_id,
                task_id=None,
                kind=ArtifactKind.TEST_REPORT,
                payload={
                    "passed": verify.passed,
                    "command": verify.command,
                    "returncode": verify.returncode,
                    "stdout": verify.stdout,
                    "stderr": verify.stderr,
                    "duration_ms": verify.duration_ms,
                    "skipped": verify.skipped,
                    "timed_out": verify.timed_out,
                },
            )
            run.artifact_ids.append(test_art.artifact_id)
            if self._store is not None:
                await self._store.save_artifact(test_art)
            await self._emit(
                run.run_id,
                events.ARTIFACT_CREATED,
                {"artifact_id": test_art.artifact_id, "kind": test_art.kind.value},
            )

            if verify.passed:
                await self._emit(
                    run.run_id,
                    events.VERIFY_PASSED,
                    {"artifact_id": test_art.artifact_id, "duration_ms": verify.duration_ms},
                )
                return verify

            await self._emit(
                run.run_id,
                events.VERIFY_FAILED,
                {
                    "artifact_id": test_art.artifact_id,
                    "returncode": verify.returncode,
                    "timed_out": verify.timed_out,
                },
            )
            if run.replan_count >= max_replans:
                await self._set_status(run, RunStatus.BLOCKED)
                await self._emit(
                    run.run_id,
                    events.RUN_BLOCKED,
                    {
                        "reason": "verify_failed_replan_exhausted",
                        "artifact_id": test_art.artifact_id,
                    },
                )
                return None

            run.replan_count += 1
            await self._append_fix_task(run=run)
            await self._emit(
                run.run_id,
                events.PLAN_CREATED,
                {"source": "verify_repair", "replan_count": run.replan_count},
            )
            await self._persist(run)

            summary = await self._executor.execute(run=run, options=options)
            if summary.failed > 0:
                await self._set_status(run, RunStatus.FAILED)
                await self._emit(
                    run.run_id,
                    events.RUN_FAILED,
                    {
                        "reason": "repair_task_failed",
                        "completed": summary.completed,
                        "failed": summary.failed,
                        "skipped": summary.skipped,
                    },
                )
                return None

            # B6/P0-4: 修复循环只集成本轮新增 patch（attempt=replan_count）
            integration = await self._integrate(run=run, attempt=run.replan_count)
            if integration.conflict:
                await self._set_status(run, RunStatus.BLOCKED)
                await self._emit(
                    run.run_id,
                    events.RUN_BLOCKED,
                    {
                        "reason": integration.reason,
                        "integration_report_id": integration.report_artifact.artifact_id,
                        "conflict_report_id": (
                            integration.conflict_artifact.artifact_id
                            if integration.conflict_artifact
                            else None
                        ),
                    },
                )
                return None

    async def _append_fix_task(self, *, run: AdaptiveRun) -> None:
        if run.task_graph is None:
            return
        existing = set(run.task_graph.nodes.keys())
        base = f"fix_{run.replan_count}"
        task_id = base
        idx = 1
        while task_id in existing:
            idx += 1
            task_id = f"{base}_{idx}"
        deps = tuple(existing)
        run.task_graph.nodes[task_id] = TaskNode(
            id=task_id,
            title=f"修复验证失败（第 {run.replan_count} 轮）",
            kind=TaskKind.WRITE,
            allowed_tools=("read_file", "edit_file", "write_file"),
            read_scope=("server",),
            write_scope=("server",),
            deps=deps,
            model_profile="smart",
            max_steps=20,
            acceptance_criteria="修复验证失败并确保命令通过",
            output_contract="patch_set",
        )

    async def _set_status(self, run: AdaptiveRun, status: RunStatus) -> None:
        """B2/P1-6: 状态机统一入口。

        - 与目标状态相同：幂等返回。
        - 优先走 store.transition_status 校验合法转换。
        - 校验失败：记录警告 + 降级直写，避免主流程因为状态机边界条件锁死；
          orchestrator 主流程按 7 步顺序推进，理论不会触发降级。
        """
        import logging

        if run.status == status:
            return
        prev = run.status
        if self._store is not None:
            try:
                updated = await self._store.transition_status(run.run_id, status)
                run.status = updated.status
                run.updated_at = updated.updated_at
                return
            except (ValueError, FileNotFoundError) as exc:
                # ValueError: 非法状态转换；FileNotFoundError: run 还未 save_run
                # 两种都降级为直接覆盖，避免主流程因边界条件锁死
                logging.getLogger(__name__).warning(
                    "Orchestrator 状态机校验失败 run=%s %s->%s err=%s，降级为直接覆盖",
                    run.run_id,
                    prev.value,
                    status.value,
                    exc,
                )
        run.status = status
        await self._persist(run)
        await self._emit(
            run.run_id,
            events.RUN_STATUS_CHANGED,
            {"from_status": prev.value, "to_status": status.value},
        )

    async def _persist(self, run: AdaptiveRun) -> None:
        run.updated_at = datetime.now(UTC)
        if self._store is None:
            return
        await self._store.save_run(run)

    async def _emit(self, run_id: str, event_type: str, payload: dict) -> None:
        event_dict: dict[str, Any] | None = None
        if self._store is None:
            event_dict = {
                "id": "",
                "run_id": run_id,
                "type": event_type,
                "ts": datetime.now(UTC).isoformat(),
                "payload": payload,
            }
        else:
            evt = await self._store.append_event(run_id, event_type, payload)
            event_dict = evt.to_dict()
        if self._event_handler is not None:
            maybe = self._event_handler(event_dict)
            if maybe is not None:
                await maybe
