"""get_artifact 工具."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from forge.orchestration.workflow.artifact_store import ArtifactStore
from forge.tools.base import Tool
from forge.tools.registry import register_tool


@register_tool
class GetArtifact(Tool):
    name = "get_artifact"
    description = "按 artifact_id 回读完整 artifact 内容 (含 payload)."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "artifact_id": {"type": "string", "description": "artifact 唯一 ID"},
            "workflow_id": {
                "type": "string",
                "description": "可选. 传入后可加速定位, 也可做范围限制",
            },
            "workspace_root": {
                "type": "string",
                "description": "可选. 指定 workspace 根路径",
            },
        },
        "required": ["artifact_id"],
    }
    required_scope = "workspace"

    async def arun(self, args: dict[str, Any]) -> dict[str, Any]:
        artifact_id = str(args.get("artifact_id") or "").strip()
        if not artifact_id:
            return {"ok": False, "error": "缺少 artifact_id"}

        workflow_id_raw = args.get("workflow_id")
        workflow_id = (
            str(workflow_id_raw).strip()
            if isinstance(workflow_id_raw, str) and workflow_id_raw.strip()
            else None
        )

        workspace_root_raw = args.get("workspace_root")
        workspace_root = (
            Path(workspace_root_raw).expanduser().resolve()
            if isinstance(workspace_root_raw, str) and workspace_root_raw.strip()
            else None
        )

        store = ArtifactStore()
        item = await store.load(
            artifact_id,
            workflow_id=workflow_id,
            workspace_root=workspace_root,
        )
        if item is None:
            return {"ok": False, "error": f"artifact 未找到: {artifact_id}"}
        return {"ok": True, "artifact": item.to_dict()}
