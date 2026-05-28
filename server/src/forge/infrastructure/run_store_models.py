"""通用 Run 持久化数据模型 (plan_exec / workflow 等 CLI 模式共用).

设计要点:
    - status 采用开放字符串, mode 自管语义 (不锁定 adaptive 的 7-state 状态机)
    - 业务状态机校验通过 StatusValidator 协议外挂, 默认放行
    - 字段集是 mode-agnostic 的最小集; mode 特有字段塞 metadata
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _now() -> datetime:
    return datetime.now(UTC)


# 通用 run 状态. mode 可以扩展自己的字面量, 但 RunStore 只识别这几个核心终态.
RUN_STATUS_RUNNING = "running"
RUN_STATUS_BLOCKED = "blocked"     # 等待 HITL 决策
RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_FAILED = "failed"
RUN_STATUS_ABORTED = "aborted"

TERMINAL_STATUSES: frozenset[str] = frozenset({
    RUN_STATUS_COMPLETED,
    RUN_STATUS_FAILED,
    RUN_STATUS_ABORTED,
})


@dataclass
class RunRecord:
    """通用 Run 元数据.

    mode 特有字段 (如 plan_markdown / workflow_template_id / current_phase)
    塞 metadata; 在 mode lifecycle 内自行维护语义.
    """

    run_id: str
    mode: str  # "plan_exec" / "workflow" / ... (chat 不用)
    workspace_path: str
    status: str = "running"
    goal: str = ""  # 用户初始目标 / 输入摘要 (可为空)
    owner_user_id: str = "local"
    artifact_ids: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "workspace_path": self.workspace_path,
            "status": self.status,
            "goal": self.goal,
            "owner_user_id": self.owner_user_id,
            "artifact_ids": list(self.artifact_ids),
            "created_at": _dt(self.created_at),
            "updated_at": _dt(self.updated_at),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RunRecord:
        return cls(
            run_id=str(payload.get("run_id", "")),
            mode=str(payload.get("mode", "")),
            workspace_path=str(payload.get("workspace_path", "")),
            status=str(payload.get("status", "running")),
            goal=str(payload.get("goal", "")),
            owner_user_id=str(payload.get("owner_user_id", "local")),
            artifact_ids=[str(v) for v in payload.get("artifact_ids", []) or []],
            created_at=_parse_dt(payload.get("created_at")) or _now(),
            updated_at=_parse_dt(payload.get("updated_at")) or _now(),
            metadata=dict(payload.get("metadata", {}) or {}),
        )


@dataclass
class Artifact:
    """通用 artifact (Agent 间通信介质).

    kind 是开放字符串. 业务上下文常用值:
        - "tool_output": 大产物落盘后回灌占位
        - "plan": plan_exec 阶段产生的计划文档
        - "report": workflow phase 产出
        - "patch_set": 代码改动集合
    """

    artifact_id: str
    run_id: str
    kind: str
    payload: Any
    task_id: str | None = None  # 关联的步骤/phase id (workflow 用)
    created_at: datetime = field(default_factory=_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "run_id": self.run_id,
            "kind": self.kind,
            "payload": self.payload,
            "task_id": self.task_id,
            "created_at": _dt(self.created_at),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Artifact:
        return cls(
            artifact_id=str(payload.get("artifact_id", "")),
            run_id=str(payload.get("run_id", "")),
            kind=str(payload.get("kind", "")),
            payload=payload.get("payload"),
            task_id=(
                str(payload["task_id"])
                if payload.get("task_id") is not None
                else None
            ),
            created_at=_parse_dt(payload.get("created_at")) or _now(),
            metadata=dict(payload.get("metadata", {}) or {}),
        )


@dataclass(frozen=True)
class RunEvent:
    """Run 事件记录, 落到 events.jsonl.

    type 字符串约定 (mode 自管词典, 这里只列基础事件):
        run_started, run_completed, run_failed, run_aborted
        step_started, step_completed
        run_status_changed
        plan_proposed, plan_approved, plan_rejected         (plan_exec)
        decision_required, decision_received                (HITL 通用)
        phase_started, phase_completed, workflow_gate_required (workflow)
    """

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


__all__ = [
    "Artifact",
    "RUN_STATUS_ABORTED",
    "RUN_STATUS_BLOCKED",
    "RUN_STATUS_COMPLETED",
    "RUN_STATUS_FAILED",
    "RUN_STATUS_RUNNING",
    "RunEvent",
    "RunRecord",
    "TERMINAL_STATUSES",
]
