"""delegate_to_agent tool."""

from __future__ import annotations

from typing import Any

from forge.core.types.errors import ToolValidationError
from forge.tools.base import Tool
from forge.tools.registry import register_tool

from .context import CURRENT_WORKFLOW_ID, CURRENT_WORKFLOW_ORCHESTRATOR


@register_tool
class DelegateToAgent(Tool):
    name = "delegate_to_agent"
    description = "把后续工作委派给指定 agent role, 并插入 workflow 的下一阶段队列."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "target_role": {"type": "string", "description": "目标 agent role"},
            "task": {"type": "string", "description": "要交给目标 role 的任务"},
            "input_artifacts": {
                "type": "array",
                "items": {"type": "string"},
                "description": "传给目标 phase 的 artifact_id 列表",
            },
        },
        "required": ["target_role", "task"],
    }
    required_scope = "workspace"

    async def arun(self, args: dict[str, Any]) -> dict[str, Any]:
        target_role = str(args.get("target_role") or "").strip().lower()
        task = str(args.get("task") or "").strip()
        if not target_role:
            raise ToolValidationError(self.name, "缺少 target_role")
        if not task:
            raise ToolValidationError(self.name, "缺少 task")
        from forge.agents.roles import get_agent_role

        get_agent_role(target_role)

        raw_artifacts = args.get("input_artifacts") or []
        if not isinstance(raw_artifacts, list):
            raise ToolValidationError(self.name, "input_artifacts 必须是数组")
        input_artifacts = [str(x) for x in raw_artifacts if isinstance(x, str) and x.strip()]

        orchestrator = CURRENT_WORKFLOW_ORCHESTRATOR.get()
        workflow_id = CURRENT_WORKFLOW_ID.get()
        if orchestrator is None or not workflow_id:
            raise ToolValidationError(self.name, "delegate_to_agent 只能在 workflow phase 内调用")

        phase = await orchestrator.enqueue_phase(
            workflow_id=workflow_id,
            role=target_role,
            task=task,
            input_artifacts=input_artifacts,
        )
        return {
            "ok": True,
            "status": "delegated",
            "workflow_id": workflow_id,
            "phase_id": phase.id,
            "target_role": target_role,
            "task": task,
            "input_artifacts": input_artifacts,
        }
