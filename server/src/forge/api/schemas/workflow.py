"""Workflow API schemas (P2 alpha)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from forge.orchestration.workflow.template_loader import TemplateLoader


def _known_template_ids() -> set[str]:
    """读最新模板列表 — 不缓存, 避免单测改模板目录后老 set 不更新."""
    return set(TemplateLoader().load_all(refresh=True).keys())


class WorkflowStartIn(BaseModel):
    message: str = Field(min_length=1)
    template_id: str | None = None
    workspace_path: str | None = None
    pause_after_phase: bool = False
    metadata: dict[str, Any] | None = None

    @field_validator("template_id")
    @classmethod
    def _validate_template_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        known = _known_template_ids()
        if v not in known:
            raise ValueError(f"未知 workflow 模板 '{v}', 可选: {sorted(known)}")
        return v


class WorkflowResumeIn(BaseModel):
    pause_after_phase: bool = False


class WorkflowAbortIn(BaseModel):
    reason: str | None = None


class WorkflowEventOut(BaseModel):
    id: str
    workflow_id: str
    type: str
    ts: str
    payload: dict[str, Any]


class WorkflowStateOut(BaseModel):
    workflow_id: str
    workspace_path: str
    template_id: str
    template_name: str
    intent: str
    input_message: str
    status: str
    current_phase_index: int
    created_at: str
    updated_at: str
    phases: list[dict[str, Any]]
    metadata: dict[str, Any]
    owner_user_id: str = "local"
