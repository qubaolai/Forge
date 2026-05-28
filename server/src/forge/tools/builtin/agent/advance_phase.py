"""advance_phase 工具 (Workflow 推进 marker).

设计同 exit_plan_mode: 工具本身只是 schema, 真实逻辑在 WorkflowLifecycle.
WorkflowLifecycle.before_tool_call 拦截后:
    - 校验 phase_id 是否匹配模板顺序
    - 命中 gate → 走 HITL workflow_gate 流程
    - 推进到下一 phase, 返回结构化提示
"""

from __future__ import annotations

from typing import Any

from forge.tools.base import Tool
from forge.tools.registry import register_tool


@register_tool
class AdvancePhase(Tool):
    name = "advance_phase"
    description = (
        "Workflow 模式专用: 当前 phase 完成后调用, 推进到下一 phase. "
        "传入当前完成的 phase id; 系统会校验顺序, 命中 gate 时阻塞等用户. "
        "Workflow 全部完成时返回 approved=True next='workflow_completed'."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "phase_id": {
                "type": "string",
                "description": "刚刚完成的 phase 的 id (与模板 phases[i].id 对齐)",
            },
            "summary": {
                "type": "string",
                "description": "(可选) 本 phase 的产出摘要, 写入事件流",
            },
        },
        "required": ["phase_id"],
    }
    parallelism_safe: bool = False
    dangerous: bool = False
    required_scope: str | None = None

    async def arun(self, args: dict[str, Any]) -> dict[str, Any]:
        """正常调用被 WorkflowLifecycle.before_tool_call 拦截, 走到这里说明 lifecycle 缺失."""
        return {
            "ok": False,
            "warning": (
                "advance_phase 直接执行了 (WorkflowLifecycle 未挂载?). "
                "phase 进度未推进, 也未触发 gate 检查."
            ),
            "phase_id": args.get("phase_id"),
        }


__all__ = ["AdvancePhase"]
