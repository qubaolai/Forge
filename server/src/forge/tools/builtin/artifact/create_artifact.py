"""create_artifact 工具 (基于通用 RunStore).

将子 agent 产物落盘为 artifact, 供下游 step / agent 读取.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from forge.core.types.errors import ToolValidationError
from forge.infrastructure.run_store import RunStore
from forge.infrastructure.run_store_models import Artifact
from forge.tools.base import Tool
from forge.tools.registry import register_tool
from forge.utils.id_generator import new_id


_ALLOWED_KINDS: tuple[str, ...] = (
    "report",
    "patch_set",
    "tool_output",
    "plan",
    "summary",
    "memo",
    "other",
)


@register_tool
class CreateArtifact(Tool):
    name = "create_artifact"
    description = (
        "在当前 run 下创建一个 artifact (Agent 间通信介质). "
        "传入 run_id + kind + title + payload, 返回 artifact_id 供下游引用."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "归属的 RunRecord.run_id"},
            "kind": {
                "type": "string",
                "description": f"artifact 类型, 推荐取值: {', '.join(_ALLOWED_KINDS)}",
            },
            "title": {"type": "string", "description": "artifact 标题"},
            "summary": {"type": "string", "description": "artifact 摘要 (≤ 200 字)"},
            "payload": {
                "type": "object",
                "description": "artifact 完整结构化内容",
            },
            "task_id": {
                "type": "string",
                "description": "(可选) 关联的 phase / step id",
            },
            "workspace_path": {
                "type": "string",
                "description": "RunStore 根路径 (默认当前 cwd)",
            },
        },
        "required": ["run_id", "kind", "title", "payload"],
    }
    required_scope = "workspace"

    async def arun(self, args: dict[str, Any]) -> dict[str, Any]:
        run_id = str(args.get("run_id") or "").strip()
        if not run_id:
            raise ToolValidationError(self.name, "缺少 run_id")
        kind = str(args.get("kind") or "").strip()
        if not kind:
            raise ToolValidationError(self.name, "缺少 kind")
        title = str(args.get("title") or "").strip()
        if not title:
            raise ToolValidationError(self.name, "缺少 title")
        payload = args.get("payload")
        if not isinstance(payload, dict):
            raise ToolValidationError(self.name, "payload 必须是 object")

        workspace_path = args.get("workspace_path")
        store = RunStore(
            workspace_path=(
                Path(workspace_path).expanduser().resolve()
                if isinstance(workspace_path, str) and workspace_path.strip()
                else None
            )
        )
        artifact = Artifact(
            artifact_id=new_id("art"),
            run_id=run_id,
            kind=kind,
            payload=payload,
            task_id=(
                str(args["task_id"])
                if args.get("task_id") is not None
                else None
            ),
            metadata={
                "title": title,
                "summary": str(args.get("summary") or "")[:200],
            },
        )
        await store.save_artifact(artifact)
        await store.append_event(
            run_id,
            "artifact_created",
            {
                "artifact_id": artifact.artifact_id,
                "kind": kind,
                "task_id": artifact.task_id,
                "title": title,
            },
        )
        return {
            "ok": True,
            "artifact_id": artifact.artifact_id,
            "run_id": run_id,
            "kind": kind,
            "title": title,
        }
