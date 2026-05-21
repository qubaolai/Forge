"""search_artifact 工具."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from forge.orchestration.workflow.artifact_store import ArtifactStore
from forge.tools.base import Tool
from forge.tools.registry import register_tool


@register_tool
class SearchArtifact(Tool):
    name = "search_artifact"
    description = (
        "按 workflow/type/role 搜索 artifact 摘要列表. "
        "仅返回 summary 元数据; 详情请再调用 get_artifact."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "workflow_id": {"type": "string", "description": "可选. 限制 workflow 范围"},
            "type": {"type": "string", "description": "可选. artifact type 过滤"},
            "role": {"type": "string", "description": "可选. created_by_role 过滤"},
            "limit": {
                "type": "integer",
                "description": "返回条数上限, 默认 20",
                "minimum": 1,
                "maximum": 200,
            },
            "workspace_root": {
                "type": "string",
                "description": "可选. 指定 workspace 根路径",
            },
        },
        "required": [],
    }
    required_scope = "workspace"

    async def arun(self, args: dict[str, Any]) -> dict[str, Any]:
        workflow_id_raw = args.get("workflow_id")
        workflow_id = (
            str(workflow_id_raw).strip()
            if isinstance(workflow_id_raw, str) and workflow_id_raw.strip()
            else None
        )
        type_raw = args.get("type")
        type_filter = (
            str(type_raw).strip() if isinstance(type_raw, str) and type_raw.strip() else None
        )
        role_raw = args.get("role")
        role_filter = (
            str(role_raw).strip() if isinstance(role_raw, str) and role_raw.strip() else None
        )
        limit_raw = args.get("limit")
        limit = int(limit_raw) if isinstance(limit_raw, int) else 20
        limit = max(1, min(limit, 200))

        workspace_root_raw = args.get("workspace_root")
        workspace_root = (
            Path(workspace_root_raw).expanduser().resolve()
            if isinstance(workspace_root_raw, str) and workspace_root_raw.strip()
            else None
        )

        store = ArtifactStore()
        results = await store.search_by_type(
            workflow_id=workflow_id,
            artifact_type=type_filter,
            role=role_filter,
            limit=limit,
            workspace_root=workspace_root,
        )
        return {
            "ok": True,
            "items": [
                {
                    "artifact_id": item.id,
                    "workflow_id": item.workflow_id,
                    "phase_id": item.phase_id,
                    "type": item.type.value,
                    "title": item.title,
                    "summary": item.summary,
                    "created_at": item.created_at.isoformat(),
                    "created_by_role": item.created_by_role,
                }
                for item in results
            ],
        }
