"""get_artifact 工具 (基于通用 RunStore).

按 artifact_id 回读完整内容. 支持跨 run 查找 (一个 workspace 下).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from forge.infrastructure.run_store import RunStore
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
            "run_id": {
                "type": "string",
                "description": "(可选) 精准定位 run, 否则跨 run 查找",
            },
            "workspace_path": {
                "type": "string",
                "description": "RunStore 根路径",
            },
        },
        "required": ["artifact_id"],
    }
    required_scope = "workspace"

    async def arun(self, args: dict[str, Any]) -> dict[str, Any]:
        artifact_id = str(args.get("artifact_id") or "").strip()
        if not artifact_id:
            return {"ok": False, "error": "缺少 artifact_id"}

        workspace_path = args.get("workspace_path")
        store = RunStore(
            workspace_path=(
                Path(workspace_path).expanduser().resolve()
                if isinstance(workspace_path, str) and workspace_path.strip()
                else None
            )
        )
        run_id = str(args.get("run_id") or "").strip()
        if run_id:
            item = await store.load_artifact(run_id, artifact_id)
        else:
            item = await store.find_artifact(artifact_id)
        if item is None:
            return {"ok": False, "error": f"artifact 未找到: {artifact_id}"}
        return {"ok": True, "artifact": item.to_dict()}
