"""Workflow artifact 模型."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


def _now() -> datetime:
    return datetime.now(UTC)


class ArtifactType(str, Enum):
    PRD = "prd"
    SRS = "srs"
    ADR = "adr"
    TASK_LIST = "task_list"
    CODE_CHANGE = "code_change"
    REVIEW_REPORT = "review_report"
    TEST_REPORT = "test_report"
    ESCALATION = "escalation"
    PHASE_OUTPUT = "phase_output"


@dataclass(frozen=True)
class Artifact:
    id: str
    workflow_id: str
    phase_id: str
    type: ArtifactType
    title: str
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_now)
    created_by_role: str = "local"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "workflow_id": self.workflow_id,
            "phase_id": self.phase_id,
            "type": self.type.value,
            "title": self.title,
            "summary": self.summary,
            "payload": dict(self.payload),
            "created_at": self.created_at.isoformat(),
            "created_by_role": self.created_by_role,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Artifact:
        data = dict(payload)
        raw_type = str(data.get("type") or ArtifactType.PHASE_OUTPUT.value)
        try:
            data["type"] = ArtifactType(raw_type)
        except ValueError:
            data["type"] = ArtifactType.PHASE_OUTPUT

        raw_created_at = data.get("created_at")
        if isinstance(raw_created_at, str):
            data["created_at"] = datetime.fromisoformat(raw_created_at)
        elif not isinstance(raw_created_at, datetime):
            data["created_at"] = _now()

        raw_payload = data.get("payload")
        if not isinstance(raw_payload, dict):
            data["payload"] = {}
        data.setdefault("created_by_role", "local")
        return cls(**data)
