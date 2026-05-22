"""TaskGraph Planner（M4）。

当前阶段目标：
- 定义可测试、可替换的规划接口。
- 支持从结构化 JSON 解析 TaskGraph。
- 在无 LLM 回包时提供保底图，保证链路可跑通。
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from forge.adaptive.models import TaskGraph, TaskKind, TaskNode
from forge.adaptive.options import TaskOptions

PlannerCallable = Callable[[str, str, list[str], TaskOptions], str | Awaitable[str]]


class PlannerError(ValueError):
    """规划失败异常。"""


@dataclass(frozen=True)
class PlannerInput:
    """规划输入快照。"""

    goal: str
    discovery_report: str
    tool_allowlist: list[str]
    options: TaskOptions


class Planner:
    """TaskGraph 规划器。"""

    def __init__(self, plan_func: PlannerCallable | None = None) -> None:
        self._plan_func = plan_func

    async def plan(
        self,
        *,
        goal: str,
        discovery_report: str,
        tool_allowlist: list[str],
        options: TaskOptions,
    ) -> TaskGraph:
        """产出 TaskGraph。"""
        if self._plan_func is None:
            return self._fallback_plan(goal=goal, options=options)

        raw = self._plan_func(goal, discovery_report, tool_allowlist, options)
        if inspect.isawaitable(raw):
            raw = await raw
        if not isinstance(raw, str):
            raise PlannerError("Planner 输出必须是 JSON 字符串")
        return self._parse_task_graph(raw)

    def _fallback_plan(self, *, goal: str, options: TaskOptions) -> TaskGraph:
        """无 LLM 可用时的兜底规划。

        说明：Step 1 DISCOVER 已由 orchestrator 单独产出 DiscoveryReport，
        因此 fallback graph 不再包含 READ-discovery 节点，避免同一 run 出现
        两份 discovery_report artifact 让前端无法区分。
        """
        now = datetime.now(UTC).isoformat()
        if not options.allow_write:
            nodes = {
                "t1": TaskNode(
                    id="t1",
                    title="整理实现建议",
                    kind=TaskKind.REVIEW,
                    allowed_tools=("read_file",),
                    read_scope=("server", "docs"),
                    write_scope=(),
                    output_contract="review_report",
                ),
            }
        else:
            nodes = {
                "t1": TaskNode(
                    id="t1",
                    title=f"实现目标: {goal[:40]}",
                    kind=TaskKind.WRITE,
                    allowed_tools=("read_file", "edit_file", "write_file"),
                    read_scope=("server",),
                    write_scope=("server",),
                    output_contract="patch_set",
                ),
                "t2": TaskNode(
                    id="t2",
                    title="执行基础验证",
                    kind=TaskKind.EXECUTE,
                    allowed_tools=("run_tests", "shell"),
                    read_scope=("server",),
                    write_scope=(),
                    deps=("t1",),
                    output_contract="test_report",
                ),
            }
        return TaskGraph(nodes=nodes, planner_raw=json.dumps({"fallback_at": now}, ensure_ascii=False))

    def _parse_task_graph(self, planner_raw: str) -> TaskGraph:
        """把 planner JSON 解析成 TaskGraph。"""
        try:
            payload = json.loads(planner_raw)
        except json.JSONDecodeError as exc:
            raise PlannerError(f"Planner 输出不是合法 JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise PlannerError("Planner 输出顶层必须是对象")

        raw_nodes = payload.get("nodes")
        if raw_nodes is None:
            raise PlannerError("Planner 输出缺少 nodes 字段")

        if isinstance(raw_nodes, list):
            data_nodes: dict[str, dict[str, Any]] = {}
            for item in raw_nodes:
                if not isinstance(item, dict):
                    raise PlannerError("nodes 列表元素必须是对象")
                task_id = str(item.get("id", "")).strip()
                if not task_id:
                    raise PlannerError("nodes 列表中的任务缺少 id")
                data_nodes[task_id] = item
        elif isinstance(raw_nodes, dict):
            data_nodes = {str(k): v for k, v in raw_nodes.items()}
        else:
            raise PlannerError("nodes 必须是对象或数组")

        parsed: dict[str, TaskNode] = {}
        for task_id, item in data_nodes.items():
            if not isinstance(item, dict):
                raise PlannerError(f"任务 {task_id} 必须是对象")
            parsed[task_id] = self._parse_node(task_id, item)

        return TaskGraph(nodes=parsed, planner_raw=planner_raw)

    @staticmethod
    def _parse_node(task_id: str, payload: dict[str, Any]) -> TaskNode:
        """解析单个 TaskNode。"""
        kind_raw = str(payload.get("kind", "")).strip().lower()
        try:
            kind = TaskKind(kind_raw)
        except ValueError as exc:
            raise PlannerError(f"任务 {task_id} kind 非法: {kind_raw!r}") from exc

        output_contract = str(payload.get("output_contract") or _default_contract(kind))
        if output_contract not in {
            "patch_set",
            "discovery_report",
            "review_report",
            "test_report",
            "integration_report",
            "final_report",
        }:
            raise PlannerError(f"任务 {task_id} output_contract 非法: {output_contract}")

        model_profile = str(payload.get("model_profile", "smart")).lower()
        if model_profile not in {"fast", "smart", "strong"}:
            raise PlannerError(f"任务 {task_id} model_profile 非法: {model_profile}")

        # N13: 拒绝 max_steps <= 0，避免任务瞬间结束或卡死
        try:
            max_steps = int(payload.get("max_steps", 25))
        except (TypeError, ValueError) as exc:
            raise PlannerError(f"任务 {task_id} max_steps 必须是整数") from exc
        if max_steps < 1:
            raise PlannerError(f"任务 {task_id} max_steps 必须 >= 1，当前: {max_steps}")

        # N19: EXECUTE 任务允许 LLM 输出 command 字段；其他 kind 忽略
        raw_command = payload.get("command")
        command = str(raw_command).strip() if raw_command else None
        if command and kind != TaskKind.EXECUTE:
            # 非 EXECUTE 任务声明 command 是设计错误，拒绝
            raise PlannerError(f"任务 {task_id} 只有 EXECUTE 类可声明 command 字段")

        return TaskNode(
            id=task_id,
            title=str(payload.get("title") or task_id),
            kind=kind,
            allowed_tools=tuple(str(v) for v in payload.get("allowed_tools", []) or []),
            read_scope=tuple(str(v) for v in payload.get("read_scope", []) or []),
            write_scope=tuple(str(v) for v in payload.get("write_scope", []) or []),
            deps=tuple(str(v) for v in payload.get("deps", []) or []),
            model_profile=model_profile,  # type: ignore[arg-type]
            max_steps=max_steps,
            acceptance_criteria=str(payload.get("acceptance_criteria", "")),
            output_contract=output_contract,  # type: ignore[arg-type]
            command=command,
        )


def _default_contract(kind: TaskKind) -> str:
    if kind == TaskKind.READ:
        return "discovery_report"
    if kind == TaskKind.REVIEW:
        return "review_report"
    if kind == TaskKind.EXECUTE:
        return "test_report"
    if kind == TaskKind.INTEGRATE:
        return "integration_report"
    return "patch_set"
