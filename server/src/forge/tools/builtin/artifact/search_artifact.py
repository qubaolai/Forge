"""search_artifact 工具 (基于通用 RunStore).

返回 artifact 摘要列表; 详情请再调用 get_artifact.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from forge.infrastructure.run_store import RunStore
from forge.tools.base import Tool
from forge.tools.registry import register_tool


@register_tool
class SearchArtifact(Tool):
    name = "search_artifact"
    description = (
        "按 run_id / kind 搜索 artifact 摘要. "
        "仅返回元数据; 详情请再调 get_artifact."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "run 范围 (必填以避免跨 run 扫描)"},
            "kind": {"type": "string", "description": "可选. artifact kind 过滤"},
            "task_id": {"type": "string", "description": "可选. task_id 过滤"},
            "limit": {
                "type": "integer",
                "description": "返回条数上限, 默认 20",
                "minimum": 1,
                "maximum": 200,
            },
            "workspace_path": {
                "type": "string",
                "description": "RunStore 根路径",
            },
        },
        "required": ["run_id"],
    }
    required_scope = "workspace"

    async def arun(self, args: dict[str, Any]) -> dict[str, Any]:
        run_id = str(args.get("run_id") or "").strip()
        if not run_id:
            return {"ok": False, "error": "缺少 run_id"}

        kind = args.get("kind")
        kind_filter = (
            str(kind).strip() if isinstance(kind, str) and kind.strip() else None
        )
        task_id = args.get("task_id")
        task_filter = (
            str(task_id).strip() if isinstance(task_id, str) and task_id.strip() else None
        )
        limit_raw = args.get("limit")
        limit = int(limit_raw) if isinstance(limit_raw, int) else 20
        limit = max(1, min(limit, 200))

        workspace_path = args.get("workspace_path")
        store = RunStore(
            workspace_path=(
                Path(workspace_path).expanduser().resolve()
                if isinstance(workspace_path, str) and workspace_path.strip()
                else None
            )
        )

        items = await store.list_artifacts(
            run_id,
            kind=kind_filter,
            task_id=task_filter,
        )
        truncated = items[:limit]
        return {
            "ok": True,
            "items": [
                {
                    "artifact_id": item.artifact_id,
                    "run_id": item.run_id,
                    "kind": item.kind,
                    "task_id": item.task_id,
                    "created_at": item.created_at.isoformat(),
                    "title": (item.metadata or {}).get("title"),
                    "summary": (item.metadata or {}).get("summary"),
                }
                for item in truncated
            ],
        }
