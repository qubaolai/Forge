"""Workflow 运行时模型 (P2 alpha)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class WorkflowPhaseTemplate:
    """模板中的 phase 定义."""

    id: str
    role: str
    task: str


@dataclass(frozen=True)
class WorkflowTemplate:
    """workflow 模板定义."""

    id: str
    name: str
    description: str
    phases: tuple[WorkflowPhaseTemplate, ...]
    mode: str = "light"
    triage_any_keywords: tuple[str, ...] = ()
    triage_priority: int = 0


@dataclass
class WorkflowPhaseState:
    """phase 运行态."""

    id: str
    role: str
    task: str
    status: str = "pending"  # pending | running | done | failed
    artifact_id: str | None = None
    input_artifact_ids: list[str] = field(default_factory=list)
    legacy_artifact: dict[str, Any] | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("legacy_artifact", None)
        if self.started_at is not None:
            d["started_at"] = self.started_at.isoformat()
        if self.completed_at is not None:
            d["completed_at"] = self.completed_at.isoformat()
        return d

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> WorkflowPhaseState:
        data = dict(payload)
        data.setdefault("artifact_id", None)
        data.setdefault("input_artifact_ids", [])
        raw_legacy = data.pop("artifact", None)
        if isinstance(raw_legacy, dict) and data.get("artifact_id") is None:
            data["legacy_artifact"] = raw_legacy
        for key in ("started_at", "completed_at"):
            v = data.get(key)
            if isinstance(v, str):
                data[key] = datetime.fromisoformat(v)
        return cls(**data)


@dataclass
class WorkflowState:
    """workflow 状态快照 (落盘到 state.json)."""

    workflow_id: str
    workspace_path: str
    template_id: str
    template_name: str
    intent: str
    input_message: str
    status: str = "running"  # running | paused | completed | aborted | failed
    current_phase_index: int = 0
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    phases: list[WorkflowPhaseState] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    owner_user_id: str = "local"

    @property
    def is_terminal(self) -> bool:
        return self.status in {"completed", "aborted", "failed"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "workspace_path": self.workspace_path,
            "template_id": self.template_id,
            "template_name": self.template_name,
            "intent": self.intent,
            "input_message": self.input_message,
            "status": self.status,
            "current_phase_index": self.current_phase_index,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "phases": [p.to_dict() for p in self.phases],
            "metadata": dict(self.metadata),
            "owner_user_id": self.owner_user_id,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> WorkflowState:
        data = dict(payload)
        for key in ("created_at", "updated_at"):
            v = data.get(key)
            if isinstance(v, str):
                data[key] = datetime.fromisoformat(v)
        data["phases"] = [WorkflowPhaseState.from_dict(p) for p in data.get("phases", [])]
        # 旧 state.json 没有 owner_user_id 字段 — 兜底为 "local" (单机历史 workflow).
        data.setdefault("owner_user_id", "local")
        if not isinstance(data.get("metadata"), dict):
            data["metadata"] = {}
        data["metadata"].setdefault("role_artifacts", {})
        return cls(**data)


@dataclass
class WorkflowEvent:
    """workflow 事件 (落盘到 events.jsonl + SSE)."""

    id: str
    workflow_id: str
    type: str
    ts: datetime
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "workflow_id": self.workflow_id,
            "type": self.type,
            "ts": self.ts.isoformat(),
            "payload": dict(self.payload),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> WorkflowEvent:
        data = dict(payload)
        ts = data.get("ts")
        if isinstance(ts, str):
            data["ts"] = datetime.fromisoformat(ts)
        return cls(**data)
