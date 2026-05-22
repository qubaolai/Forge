"""AdaptiveRun / TaskGraph / TaskNode 核心数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal


def _now() -> datetime:
    return datetime.now(UTC)


class RunStatus(str, Enum):
    CREATED = "created"
    PLANNING = "planning"
    VALIDATING = "validating"
    EXECUTING = "executing"
    INTEGRATING = "integrating"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    ABORTED = "aborted"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class TaskKind(str, Enum):
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    REVIEW = "review"
    INTEGRATE = "integrate"


class ArtifactKind(str, Enum):
    DISCOVERY_REPORT = "discovery_report"
    TASK_GRAPH = "task_graph"
    PATCH_SET = "patch_set"
    REVIEW_REPORT = "review_report"
    TEST_REPORT = "test_report"
    INTEGRATION_REPORT = "integration_report"
    CONFLICT_REPORT = "conflict_report"
    FINAL_REPORT = "final_report"


# N16: Literal 字段的反序列化白名单
_MODEL_PROFILES: frozenset[str] = frozenset({"fast", "smart", "strong"})
_OUTPUT_CONTRACTS: frozenset[str] = frozenset({
    "patch_set",
    "discovery_report",
    "review_report",
    "test_report",
    "integration_report",
    "final_report",
})


@dataclass(frozen=True)
class TaskNode:
    id: str
    title: str
    kind: TaskKind

    allowed_tools: tuple[str, ...]
    read_scope: tuple[str, ...]
    write_scope: tuple[str, ...]

    deps: tuple[str, ...] = ()
    model_profile: Literal["fast", "smart", "strong"] = "smart"
    max_steps: int = 25
    acceptance_criteria: str = ""
    output_contract: Literal[
        "patch_set", "discovery_report", "review_report",
        "test_report", "integration_report", "final_report"
    ] = "patch_set"

    # N19: EXECUTE 任务专用——Planner 输出的 shell 命令
    # 其他 kind 忽略；executor 优先 node.command，fallback 到 options.verifier_cmd
    command: str | None = None

    status: TaskStatus = TaskStatus.PENDING
    artifact_ids: tuple[str, ...] = ()
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind.value,
            "allowed_tools": list(self.allowed_tools),
            "read_scope": list(self.read_scope),
            "write_scope": list(self.write_scope),
            "deps": list(self.deps),
            "model_profile": self.model_profile,
            "max_steps": self.max_steps,
            "acceptance_criteria": self.acceptance_criteria,
            "output_contract": self.output_contract,
            "command": self.command,
            "status": self.status.value,
            "artifact_ids": list(self.artifact_ids),
            "error": self.error,
            "started_at": _dt(self.started_at),
            "completed_at": _dt(self.completed_at),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TaskNode:
        # N16: Literal 字段在 Python 运行时不强校验，反序列化时显式做白名单
        model_profile = str(payload.get("model_profile", "smart"))
        if model_profile not in _MODEL_PROFILES:
            raise ValueError(
                f"非法 model_profile: {model_profile!r}，允许: {sorted(_MODEL_PROFILES)}"
            )
        output_contract = str(payload.get("output_contract", "patch_set"))
        if output_contract not in _OUTPUT_CONTRACTS:
            raise ValueError(
                f"非法 output_contract: {output_contract!r}，允许: {sorted(_OUTPUT_CONTRACTS)}"
            )
        return cls(
            id=str(payload.get("id", "")),
            title=str(payload.get("title", "")),
            kind=TaskKind(str(payload.get("kind", TaskKind.READ.value))),
            allowed_tools=tuple(str(v) for v in payload.get("allowed_tools", []) or []),
            read_scope=tuple(str(v) for v in payload.get("read_scope", []) or []),
            write_scope=tuple(str(v) for v in payload.get("write_scope", []) or []),
            deps=tuple(str(v) for v in payload.get("deps", []) or []),
            model_profile=model_profile,  # type: ignore[assignment]
            max_steps=int(payload.get("max_steps", 25)),
            acceptance_criteria=str(payload.get("acceptance_criteria", "")),
            output_contract=output_contract,  # type: ignore[assignment]
            command=(str(payload["command"]) if payload.get("command") else None),
            status=TaskStatus(str(payload.get("status", TaskStatus.PENDING.value))),
            artifact_ids=tuple(str(v) for v in payload.get("artifact_ids", []) or []),
            error=(str(payload["error"]) if payload.get("error") else None),
            started_at=_parse_dt(payload.get("started_at")),
            completed_at=_parse_dt(payload.get("completed_at")),
        )


@dataclass
class TaskGraph:
    nodes: dict[str, TaskNode]
    planner_raw: str = ""
    created_at: datetime = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": {task_id: node.to_dict() for task_id, node in self.nodes.items()},
            "planner_raw": self.planner_raw,
            "created_at": _dt(self.created_at),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TaskGraph:
        raw_nodes = payload.get("nodes", {}) or {}
        nodes = {str(k): TaskNode.from_dict(v) for k, v in raw_nodes.items()}
        return cls(
            nodes=nodes,
            planner_raw=str(payload.get("planner_raw", "")),
            created_at=_parse_dt(payload.get("created_at")) or _now(),
        )


@dataclass
class AdaptiveRun:
    run_id: str
    workspace_path: str
    goal: str
    status: RunStatus = RunStatus.CREATED
    task_graph: TaskGraph | None = None
    current_wave: int = 0
    replan_count: int = 0
    artifact_ids: list[str] = field(default_factory=list)
    owner_user_id: str = "local"
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    metadata: dict[str, Any] = field(default_factory=dict)
    # 持久化 TaskOptions 快照，用于 decide continue / 服务重启后恢复执行上下文
    options_snapshot: dict[str, Any] | None = None

    def can_transition_to(self, to_status: RunStatus) -> bool:
        if self.status == to_status:
            return True
        return to_status in _RUN_STATUS_TRANSITIONS[self.status]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "workspace_path": self.workspace_path,
            "goal": self.goal,
            "status": self.status.value,
            "task_graph": self.task_graph.to_dict() if self.task_graph else None,
            "current_wave": self.current_wave,
            "replan_count": self.replan_count,
            "artifact_ids": list(self.artifact_ids),
            "owner_user_id": self.owner_user_id,
            "created_at": _dt(self.created_at),
            "updated_at": _dt(self.updated_at),
            "metadata": dict(self.metadata),
            "options_snapshot": dict(self.options_snapshot) if self.options_snapshot else None,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> AdaptiveRun:
        raw_task_graph = payload.get("task_graph")
        raw_options = payload.get("options_snapshot")
        return cls(
            run_id=str(payload.get("run_id", "")),
            workspace_path=str(payload.get("workspace_path", "")),
            goal=str(payload.get("goal", "")),
            status=RunStatus(str(payload.get("status", RunStatus.CREATED.value))),
            task_graph=TaskGraph.from_dict(raw_task_graph) if isinstance(raw_task_graph, dict) else None,
            current_wave=int(payload.get("current_wave", 0)),
            replan_count=int(payload.get("replan_count", 0)),
            artifact_ids=[str(v) for v in payload.get("artifact_ids", []) or []],
            owner_user_id=str(payload.get("owner_user_id", "local")),
            created_at=_parse_dt(payload.get("created_at")) or _now(),
            updated_at=_parse_dt(payload.get("updated_at")) or _now(),
            metadata=dict(payload.get("metadata", {}) or {}),
            options_snapshot=dict(raw_options) if isinstance(raw_options, dict) else None,
        )


@dataclass
class Artifact:
    artifact_id: str
    run_id: str
    task_id: str | None
    kind: ArtifactKind
    payload: Any
    created_at: datetime = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "kind": self.kind.value,
            "payload": self.payload,
            "created_at": _dt(self.created_at),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Artifact:
        return cls(
            artifact_id=str(payload.get("artifact_id", "")),
            run_id=str(payload.get("run_id", "")),
            task_id=(str(payload["task_id"]) if payload.get("task_id") else None),
            kind=ArtifactKind(str(payload.get("kind", ArtifactKind.REVIEW_REPORT.value))),
            payload=payload.get("payload"),
            created_at=_parse_dt(payload.get("created_at")) or _now(),
        )


@dataclass(frozen=True)
class RunEvent:
    """AdaptiveRun 事件记录。"""

    id: str
    run_id: str
    type: str
    ts: datetime
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "type": self.type,
            "ts": _dt(self.ts),
            "payload": dict(self.payload),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RunEvent:
        return cls(
            id=str(payload.get("id", "")),
            run_id=str(payload.get("run_id", "")),
            type=str(payload.get("type", "")),
            ts=_parse_dt(payload.get("ts")) or _now(),
            payload=dict(payload.get("payload", {}) or {}),
        )


_RUN_STATUS_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.CREATED: {RunStatus.PLANNING, RunStatus.ABORTED, RunStatus.FAILED},
    RunStatus.PLANNING: {RunStatus.VALIDATING, RunStatus.BLOCKED, RunStatus.ABORTED, RunStatus.FAILED},
    RunStatus.VALIDATING: {
        RunStatus.PLANNING,
        RunStatus.EXECUTING,
        RunStatus.BLOCKED,
        RunStatus.ABORTED,
        RunStatus.FAILED,
    },
    RunStatus.EXECUTING: {RunStatus.INTEGRATING, RunStatus.BLOCKED, RunStatus.ABORTED, RunStatus.FAILED},
    RunStatus.INTEGRATING: {RunStatus.VERIFYING, RunStatus.BLOCKED, RunStatus.ABORTED, RunStatus.FAILED},
    RunStatus.VERIFYING: {
        RunStatus.EXECUTING,
        RunStatus.COMPLETED,
        RunStatus.BLOCKED,
        RunStatus.ABORTED,
        RunStatus.FAILED,
    },
    RunStatus.COMPLETED: set(),
    RunStatus.FAILED: set(),
    RunStatus.BLOCKED: {RunStatus.PLANNING, RunStatus.EXECUTING, RunStatus.ABORTED, RunStatus.FAILED},
    RunStatus.ABORTED: set(),
}


def _dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _parse_dt(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value)
    return None
