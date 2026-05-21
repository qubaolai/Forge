"""AdaptiveRun / Artifact API schemas。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from forge.adaptive.models import ArtifactKind, RunStatus, TaskKind, TaskStatus
from forge.adaptive.options import TaskOptionsIn


class RunCreateIn(BaseModel):
    goal: str = Field(min_length=1)
    task_options: TaskOptionsIn | None = None


class TaskNodeOut(BaseModel):
    id: str
    title: str
    kind: TaskKind
    deps: list[str]
    model_profile: Literal["fast", "smart", "strong"]
    max_steps: int
    status: TaskStatus
    artifact_ids: list[str]
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class TaskGraphOut(BaseModel):
    nodes: dict[str, TaskNodeOut]
    created_at: datetime


class RunOut(BaseModel):
    run_id: str
    workspace_path: str
    goal: str
    status: RunStatus
    task_graph: TaskGraphOut | None = None
    current_wave: int
    replan_count: int
    artifact_ids: list[str]
    owner_user_id: str
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = {}


class RunListOut(BaseModel):
    items: list[RunOut]
    total: int


class ArtifactOut(BaseModel):
    artifact_id: str
    run_id: str
    task_id: str | None
    kind: ArtifactKind
    payload: Any
    created_at: datetime


class DecideIn(BaseModel):
    """BLOCKED 状态下人工决策。"""

    decision: Literal["continue", "abort"]
    notes: str | None = None
