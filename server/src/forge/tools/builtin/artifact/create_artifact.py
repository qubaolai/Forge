"""create_artifact 工具."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from forge.core.types.errors import ToolValidationError
from forge.orchestration.workflow.artifact import Artifact, ArtifactType
from forge.orchestration.workflow.artifact_store import ArtifactStore
from forge.tools.base import Tool
from forge.tools.registry import register_tool
from forge.utils.id_generator import new_id


@register_tool
class CreateArtifact(Tool):
    name = "create_artifact"
    description = (
        "创建一个 workflow artifact 并落盘. 需要 workflow_id, 返回 artifact_id 供后续引用."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "workflow_id": {"type": "string", "description": "目标 workflow_id"},
            "phase_id": {"type": "string", "description": "所属 phase_id, 默认 manual"},
            "created_by_role": {"type": "string", "description": "创建角色, 默认 local"},
            "type": {
                "type": "string",
                "enum": [t.value for t in ArtifactType],
                "description": "artifact 类型",
            },
            "title": {"type": "string", "description": "artifact 标题"},
            "summary": {"type": "string", "description": "artifact 摘要"},
            "payload": {"type": "object", "description": "artifact 完整结构化内容"},
            "workspace_root": {
                "type": "string",
                "description": "可选. 指定 workspace 根路径, 不传则自动按 workflow_id 查找",
            },
        },
        "required": ["type", "title", "summary", "payload"],
    }
    required_scope = "workspace"

    async def arun(self, args: dict[str, Any]) -> dict[str, Any]:
        workflow_id = str(args.get("workflow_id") or "").strip()
        if not workflow_id:
            raise ToolValidationError(self.name, "缺少 workflow_id")

        type_raw = str(args.get("type") or "").strip().lower()
        try:
            artifact_type = ArtifactType(type_raw)
        except ValueError as exc:
            raise ToolValidationError(self.name, f"未知 artifact type: {type_raw}") from exc

        title = str(args.get("title") or "").strip()
        summary = str(args.get("summary") or "").strip()
        payload = args.get("payload")
        if not isinstance(payload, dict):
            raise ToolValidationError(self.name, "payload 必须是 object")

        artifact = Artifact(
            id=new_id("art"),
            workflow_id=workflow_id,
            phase_id=str(args.get("phase_id") or "manual"),
            type=artifact_type,
            title=title,
            summary=summary,
            payload=payload,
            created_by_role=str(args.get("created_by_role") or "local"),
        )
        store = ArtifactStore()
        workspace_root_raw = args.get("workspace_root")
        if isinstance(workspace_root_raw, str) and workspace_root_raw.strip():
            await store.save(
                artifact,
                workspace_root=Path(workspace_root_raw).expanduser().resolve(),
            )
        else:
            await store.save(artifact)

        return {
            "ok": True,
            "artifact_id": artifact.id,
            "workflow_id": artifact.workflow_id,
            "phase_id": artifact.phase_id,
            "type": artifact.type.value,
            "title": artifact.title,
            "summary": artifact.summary,
        }
