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

    status: TaskStatus = TaskStatus.PENDING
    artifact_ids: tuple[str, ...] = ()
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass
class TaskGraph:
    nodes: dict[str, TaskNode]
    planner_raw: str = ""
    created_at: datetime = field(default_factory=_now)


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


@dataclass
class Artifact:
    artifact_id: str
    run_id: str
    task_id: str | None
    kind: ArtifactKind
    payload: Any
    created_at: datetime = field(default_factory=_now)
