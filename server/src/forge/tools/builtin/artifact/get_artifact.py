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
    description = (
        "按 artifact_id 回读 artifact 内容 (含 payload)。可选 line_range=[起始行,结束行] "
        "(1-based 闭区间) 只取 payload 文本的某片段, 避免大产物整体拉回爆窗。"
    )
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
            "line_range": {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 2,
                "maxItems": 2,
                "description": "(可选) 1-based 闭区间 [起始行, 结束行]; 仅切 payload 文本",
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

        line_range = _parse_line_range(args.get("line_range"))
        if line_range is not None:
            # 定向回读: 只返回 payload 文本的指定行区间
            from forge.infrastructure.storage.content_store import (
                payload_to_text,
                slice_text,
            )

            sl = slice_text(payload_to_text(item.payload), line_range)
            return {
                "ok": True,
                "artifact_id": artifact_id,
                "text": sl.text,
                "total_lines": sl.total_lines,
                "returned_range": list(sl.returned_range),
                "truncated": sl.truncated,
            }
        return {"ok": True, "artifact": item.to_dict()}


def _parse_line_range(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, list | tuple) or len(value) != 2:
        return None
    try:
        start, end = int(value[0]), int(value[1])
    except (TypeError, ValueError):
        return None
    if start <= 0 or end <= 0:
        return None
    return start, end
