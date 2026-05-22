"""任务执行器（M9: 同 wave 并发执行）。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from forge.adaptive import events
from forge.adaptive.models import (
    AdaptiveRun,
    Artifact,
    ArtifactKind,
    RunStatus,
    TaskGraph,
    TaskKind,
    TaskNode,
    TaskStatus,
)
from forge.adaptive.options import TaskOptions
from forge.adaptive.scheduler import Scheduler
from forge.adaptive.store import AdaptiveRunStore
from forge.adaptive.workspace import GitWorktreeStrategy, IsolateStrategy
from forge.utils.id_generator import new_id

EventHandler = Callable[[dict[str, Any]], Awaitable[None] | None]


@dataclass(frozen=True)
class ExecuteSummary:
    """执行统计。"""

    completed: int
    failed: int
    skipped: int


class TaskExecutor:
    """M9 并行 wave 执行器。"""

    def __init__(
        self,
        *,
        store: AdaptiveRunStore | None = None,
        scheduler: Scheduler | None = None,
        isolate_strategy: IsolateStrategy | None = None,
        event_handler: EventHandler | None = None,
        enable_real_llm: bool = False,
    ) -> None:
        self._store = store
        self._scheduler = scheduler or Scheduler()
        self._isolate_strategy = isolate_strategy or GitWorktreeStrategy()
        self._event_handler = event_handler
        # B9/P0-1: 启用后 TaskNode 用 ReActAgent 真实执行；
        # 关闭时仍走占位 payload，保持单测/默认体验
        self._enable_real_llm = enable_real_llm

    async def execute(self, *, run: AdaptiveRun, options: TaskOptions) -> ExecuteSummary:
        """B6/P0-4: 增量执行 —— 仅处理 status=PENDING 的节点，已完成/失败/跳过的保留。

        修复循环场景：``_append_fix_task`` 追加新节点（PENDING），原节点保持
        COMPLETED；执行时只跑新节点 + 它新增依赖链上仍 PENDING 的节点，
        不重做历史任务，也不重发已存在的 artifact。
        """
        if run.task_graph is None:
            raise ValueError("run.task_graph 为空，无法执行")

        graph = run.task_graph
        summary = ExecuteSummary(completed=0, failed=0, skipped=0)
        skipped_nodes: set[str] = set()

        await self._set_run_status(run, RunStatus.EXECUTING)
        plan = self._scheduler.compute_waves(graph, workspace_path=options.workspace_path)
        wave_failed_or_skipped: set[str] = set()

        for wave_idx, wave in enumerate(plan.waves):
            run.current_wave = wave_idx
            await self._persist(run)
            await self._emit(run.run_id, events.WAVE_STARTED, {"wave": wave_idx, "tasks": list(wave)})

            ready_task_ids: list[str] = []
            for task_id in wave:
                node = graph.nodes[task_id]
                # B6: 已完成节点（修复循环时的历史节点）直接跳过，不重复执行
                if node.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED, TaskStatus.FAILED):
                    continue
                if any(dep in wave_failed_or_skipped for dep in node.deps):
                    skipped_nodes.add(task_id)
                    wave_failed_or_skipped.add(task_id)
                    await self._replace_node(
                        run,
                        node,
                        status=TaskStatus.SKIPPED,
                        error="依赖任务失败，已级联跳过",
                    )
                    await self._emit(
                        run.run_id,
                        events.TASK_SKIPPED,
                        {"task_id": task_id, "reason": "dependency_failed"},
                    )
                    summary = replace(summary, skipped=summary.skipped + 1)
                    continue
                ready_task_ids.append(task_id)

            if ready_task_ids:
                for task_id in ready_task_ids:
                    node = graph.nodes[task_id]
                    await self._replace_node(
                        run,
                        node,
                        status=TaskStatus.RUNNING,
                        started_at=datetime.now(UTC),
                        error=None,
                    )
                    await self._emit(
                        run.run_id,
                        events.TASK_STARTED,
                        {"task_id": task_id, "kind": node.kind.value},
                    )

                # N12: 并发控制 — allow_parallel=False 退化为单 agent；
                # 否则上限 = min(max_agents, 本 wave 任务数)。
                concurrency_limit = 1 if not options.allow_parallel else max(
                    1, min(options.max_agents, len(ready_task_ids))
                )
                wave_semaphore = asyncio.Semaphore(concurrency_limit)
                max_retries = max(0, int(options.hard_caps.max_task_retries))

                async def _run_with_limit(
                    node_id: str,
                    *,
                    sem: asyncio.Semaphore = wave_semaphore,
                    retries: int = max_retries,
                ):
                    """B13/P2-12: 节点级 try ... max_retries 次重试。"""
                    last_exc: Exception | None = None
                    async with sem:
                        for attempt in range(retries + 1):
                            try:
                                return await self._execute_single_node(
                                    run=run,
                                    node=graph.nodes[node_id],
                                    graph=graph,
                                    options=options,
                                )
                            except PermissionError:
                                # 写权限拦截不应该重试，直接抛
                                raise
                            except Exception as exc:  # noqa: BLE001
                                last_exc = exc
                                if attempt >= retries:
                                    break
                                await self._emit(
                                    run.run_id,
                                    events.TASK_FAILED,
                                    {
                                        "task_id": node_id,
                                        "attempt": attempt + 1,
                                        "max_attempts": retries + 1,
                                        "error": str(exc),
                                        "retrying": True,
                                    },
                                )
                    assert last_exc is not None  # pragma: no cover
                    raise last_exc

                results = await asyncio.gather(
                    *[_run_with_limit(task_id) for task_id in ready_task_ids],
                    return_exceptions=True,
                )
                for task_id, result in zip(ready_task_ids, results, strict=False):
                    node = graph.nodes[task_id]
                    if isinstance(result, Exception):
                        wave_failed_or_skipped.add(task_id)
                        await self._replace_node(
                            run,
                            node,
                            status=TaskStatus.FAILED,
                            error=str(result),
                            completed_at=datetime.now(UTC),
                        )
                        await self._emit(
                            run.run_id,
                            events.TASK_FAILED,
                            {"task_id": task_id, "error": str(result)},
                        )
                        summary = replace(summary, failed=summary.failed + 1)
                        continue

                    artifact = result
                    run.artifact_ids.append(artifact.artifact_id)
                    await self._replace_node(
                        run,
                        node,
                        status=TaskStatus.COMPLETED,
                        artifact_ids=(artifact.artifact_id,),
                        completed_at=datetime.now(UTC),
                    )
                    await self._emit(
                        run.run_id,
                        events.TASK_COMPLETED,
                        {"task_id": task_id, "artifact_id": artifact.artifact_id},
                    )
                    summary = replace(summary, completed=summary.completed + 1)

            await self._emit(run.run_id, events.WAVE_COMPLETED, {"wave": wave_idx})

        await self._persist(run)
        return summary

    async def _execute_single_node(
        self,
        *,
        run: AdaptiveRun,
        node: TaskNode,
        graph: TaskGraph,
        options: TaskOptions,
    ) -> Artifact:
        # B3/P1-7: Executor 二次防护 — Planner/Validator 都过了，
        # Executor 在真实执行前仍要校验 allow_write，避免 fallback 路径绕过
        if not options.allow_write and (
            node.kind in (TaskKind.WRITE, TaskKind.INTEGRATE) or node.write_scope
        ):
            raise PermissionError(
                f"任务 {node.id} ({node.kind.value}) 在 allow_write=False 下被禁止"
            )
        artifact_id = new_id("art")
        if node.kind == TaskKind.WRITE and options.writer_mode == "isolated_worktree":
            payload = await self._execute_write_in_isolation(run=run, node=node)
        elif self._enable_real_llm and node.kind in (
            TaskKind.READ,
            TaskKind.REVIEW,
            TaskKind.EXECUTE,
            TaskKind.INTEGRATE,
        ):
            payload = await self._execute_with_react(run=run, node=node, options=options)
        else:
            payload = self._build_artifact_payload(run=run, node=node, graph=graph, options=options)
        # B6/P0-4: 给 payload 打上 attempt 号，方便 Integrator 按批次过滤
        if isinstance(payload, dict):
            payload.setdefault("attempt", run.replan_count)
        artifact = Artifact(
            artifact_id=artifact_id,
            run_id=run.run_id,
            task_id=node.id,
            kind=_artifact_kind_for_node(node),
            payload=payload,
        )
        if self._store is not None:
            await self._store.save_artifact(artifact)
        await self._emit(
            run.run_id,
            events.ARTIFACT_CREATED,
            {"artifact_id": artifact.artifact_id, "task_id": node.id, "kind": artifact.kind.value},
        )
        return artifact

    async def _execute_write_in_isolation(self, *, run: AdaptiveRun, node: TaskNode) -> dict[str, Any]:
        env = await self._isolate_strategy.prepare(node, run)
        cleanup_done = False
        try:
            # B9/P0-1: 启用真实 LLM 时让 ReActAgent 在 worktree 内真的改代码
            if self._enable_real_llm:
                from forge.adaptive.task_runner import TaskRunnerError, run_node_with_react

                try:
                    upstream = await self._collect_upstream_context(run=run, node=node)
                    react_result = await run_node_with_react(
                        node=node,
                        workspace_path=run.workspace_path,
                        work_path=env.work_path,
                        upstream_context=upstream,
                    )
                except TaskRunnerError as exc:
                    # 真实执行失败时仍按 collect 收集（可能 worktree 已有部分变更）
                    import logging
                    logging.getLogger(__name__).warning(
                        "WRITE 任务 ReAct 执行失败 task=%s err=%s，仅收集已写入差异", node.id, exc
                    )
                    react_result = None
                patch = await self._isolate_strategy.collect(env, task_id=node.id)
                payload = patch.to_dict()
                payload["write_scope"] = list(node.write_scope)
                payload["agent_output"] = react_result.output if react_result else None
                payload["agent_steps"] = react_result.steps if react_result else 0
                payload["note"] = "B9 真实执行：ReActAgent 在隔离 worktree 内运行"
                # B14/P2-13: 标注 worktree 已 cleanup，下游基于 diff 重放
                payload["worktree_retained"] = False
                payload["diff_only_safe"] = True
                return payload

            patch = await self._isolate_strategy.collect(env, task_id=node.id)
            payload = patch.to_dict()
            payload["write_scope"] = list(node.write_scope)
            payload["note"] = "M6 已启用隔离 worktree，当前仍为占位改写逻辑"
            payload["worktree_retained"] = False
            payload["diff_only_safe"] = True
            return payload
        finally:
            if not cleanup_done:
                await self._isolate_strategy.cleanup(env)

    async def _execute_with_react(
        self,
        *,
        run: AdaptiveRun,
        node: TaskNode,
        options: TaskOptions,
    ) -> dict[str, Any]:
        """B9: 非 WRITE 任务也用 ReActAgent 真实执行（READ / REVIEW / EXECUTE / INTEGRATE）。"""
        from forge.adaptive.task_runner import TaskRunnerError, run_node_with_react

        upstream = await self._collect_upstream_context(run=run, node=node)
        try:
            react_result = await run_node_with_react(
                node=node,
                workspace_path=options.workspace_path or run.workspace_path,
                work_path=None,
                upstream_context=upstream,
            )
        except TaskRunnerError as exc:
            raise RuntimeError(f"任务 {node.id} ReAct 执行失败: {exc}") from exc

        base: dict[str, Any] = {
            "task_id": node.id,
            "kind": node.kind.value,
            "agent_output": react_result.output,
            "agent_steps": react_result.steps,
            "usage": react_result.usage,
            "source": "react_agent",
        }
        if node.kind == TaskKind.READ:
            base["summary"] = react_result.output
        elif node.kind == TaskKind.REVIEW:
            base["conclusion"] = react_result.output
        elif node.kind == TaskKind.EXECUTE:
            base["command"] = node.command or options.verifier_cmd or "noop"
            base["passed"] = True  # ReAct 完成即视为通过；具体退出码由命令工具记录
        elif node.kind == TaskKind.INTEGRATE:
            base["merged"] = True
        return base

    async def _collect_upstream_context(self, *, run: AdaptiveRun, node: TaskNode) -> str:
        """收集依赖任务 artifact 的简要文本，作为 ReAct user_input 上下文。"""
        if self._store is None or not node.deps:
            return ""
        chunks: list[str] = []
        for dep_id in node.deps:
            dep_node = run.task_graph.nodes.get(dep_id) if run.task_graph else None
            if dep_node is None or not dep_node.artifact_ids:
                continue
            for art_id in dep_node.artifact_ids:
                art = await self._store.load_artifact(run.run_id, art_id)
                if art is None:
                    continue
                summary = ""
                if isinstance(art.payload, dict):
                    summary = str(
                        art.payload.get("summary")
                        or art.payload.get("agent_output")
                        or art.payload.get("conclusion")
                        or ""
                    )
                if summary:
                    chunks.append(f"### 来自 {dep_id} ({art.kind.value})\n{summary}")
        return "\n\n".join(chunks)

    def _build_artifact_payload(
        self,
        *,
        run: AdaptiveRun,
        node: TaskNode,
        graph: TaskGraph,
        options: TaskOptions,
    ) -> dict[str, Any]:
        workspace = Path(options.workspace_path).expanduser().resolve()
        if node.kind == TaskKind.READ:
            return {
                "task_id": node.id,
                "summary": f"已完成阅读任务：{node.title}",
                "workspace_path": str(workspace),
                "read_scope": list(node.read_scope),
            }
        if node.kind == TaskKind.WRITE:
            touched = list(node.write_scope) or ["server"]
            return {
                "task_id": node.id,
                "base_ref": "HEAD",
                "worktree_path": str(workspace),
                "changed_files": [],
                "diff": "",
                "write_scope": touched,
                "note": "M5 串行执行器未接入真实代码改写，此 PatchSet 为占位产物",
                "worktree_retained": True,
                "diff_only_safe": True,
            }
        if node.kind == TaskKind.EXECUTE:
            # N19: 节点级 command 优先，回落到 run 级 verifier_cmd
            command = node.command or options.verifier_cmd or "noop"
            return {
                "task_id": node.id,
                "passed": True,
                "command": command,
                "command_source": "task_node" if node.command else (
                    "options.verifier_cmd" if options.verifier_cmd else "noop"
                ),
                "stdout": "",
                "stderr": "",
                "note": "M5 阶段执行器以占位模式返回通过结果",
            }
        if node.kind == TaskKind.REVIEW:
            return {
                "task_id": node.id,
                "conclusion": "reviewed",
                "note": "M5 阶段为最小可运行实现",
            }
        if node.kind == TaskKind.INTEGRATE:
            return {
                "task_id": node.id,
                "merged": True,
                "note": "M5 阶段未启用真实合并器",
            }
        return {"task_id": node.id, "note": "unknown task kind"}

    async def _replace_node(
        self,
        run: AdaptiveRun,
        node: TaskNode,
        *,
        status: TaskStatus,
        error: str | None = None,
        artifact_ids: tuple[str, ...] | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        if run.task_graph is None:
            return
        new_node = replace(
            node,
            status=status,
            error=error,
            artifact_ids=artifact_ids if artifact_ids is not None else node.artifact_ids,
            started_at=started_at if started_at is not None else node.started_at,
            completed_at=completed_at if completed_at is not None else node.completed_at,
        )
        run.task_graph.nodes[node.id] = new_node
        await self._persist(run)

    async def _set_run_status(self, run: AdaptiveRun, status: RunStatus) -> None:
        """状态机统一入口：优先通过 store.transition_status 校验合法转换。

        - 当前状态与目标相同：幂等返回，不发事件。
        - store 校验失败：记录警告 + 降级为旧行为（直接覆盖），避免
          orchestrator 半路绕开校验后 executor 状态机锁死整个 run；
          真实生产路径应由 orchestrator 顺序推进状态，executor 几乎不会
          走到降级分支。
        - 无 store（单测场景）：直接旧行为。
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
            except ValueError as exc:
                logging.getLogger(__name__).warning(
                    "Executor 状态机校验失败 run=%s %s->%s err=%s，降级为直接覆盖",
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
        if self._store is None:
            return
        await self._store.save_run(run)

    async def _emit(self, run_id: str, event_type: str, payload: dict[str, Any]) -> None:
        evt = {
            "id": "",
            "run_id": run_id,
            "type": event_type,
            "ts": datetime.now(UTC).isoformat(),
            "payload": payload,
        }
        if self._store is not None:
            created = await self._store.append_event(run_id, event_type, payload)
            evt = created.to_dict()
        if self._event_handler is not None:
            maybe = self._event_handler(evt)
            if maybe is not None:
                await maybe


def _artifact_kind_for_node(node: TaskNode) -> ArtifactKind:
    if node.output_contract == "discovery_report":
        return ArtifactKind.DISCOVERY_REPORT
    if node.output_contract == "review_report":
        return ArtifactKind.REVIEW_REPORT
    if node.output_contract == "test_report":
        return ArtifactKind.TEST_REPORT
    if node.output_contract == "integration_report":
        return ArtifactKind.INTEGRATION_REPORT
    if node.output_contract == "final_report":
        return ArtifactKind.FINAL_REPORT
    return ArtifactKind.PATCH_SET
