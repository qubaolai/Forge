"""exit_plan_mode 工具 (Plan Mode → Exec Mode 的 gate).

设计要点 (与一般工具不同):
    - **工具本身只是 schema 暴露给 LLM** — LLM 看见这个工具就知道"完成探索
      后该调用它来呈现计划".
    - **真实 HITL 逻辑在 PlanModeLifecycle.before_tool_call 中**, 通过
      ToolCallVeto 拦截掉真实执行, 自己处理 DecisionRegistry + 等待回包.
    - 因此 arun 默认抛错: 任何在 Plan Mode 之外的环境调用此工具都是配置错误.

参数:
    plan_markdown: 完整的 Markdown 计划文档 (含目标 / 步骤 / 影响面 / 验证)
"""

from __future__ import annotations

from typing import Any

from forge.core.types.errors import ToolValidationError
from forge.tools.base import Tool
from forge.tools.registry import register_tool


@register_tool
class ExitPlanMode(Tool):
    name = "exit_plan_mode"
    description = (
        "完成 Plan Mode 的规划后调用此工具, 提交计划等待用户批准. "
        "传入 plan_markdown (含目标/步骤/影响文件/验证方式). "
        "用户批准后写工具会自动解锁, 你可以继续按计划执行."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "plan_markdown": {
                "type": "string",
                "description": (
                    "完整的 Markdown 格式执行计划. 建议包含 4 节: "
                    "## 目标 / ## 步骤 / ## 影响面 / ## 验证方式"
                ),
            }
        },
        "required": ["plan_markdown"],
    }
    parallelism_safe: bool = False  # HITL gate, 绝不并行
    dangerous: bool = False
    required_scope: str | None = None

    async def arun(self, args: dict[str, Any]) -> dict[str, Any]:
        """正常调用路径会被 PlanModeLifecycle.before_tool_call 拦截.
        走到这里说明 lifecycle 未挂载 (配置错误).
        """
        plan = str(args.get("plan_markdown") or "").strip()
        if not plan:
            raise ToolValidationError(self.name, "缺少 plan_markdown")
        # 兜底: lifecycle 缺失时直接当作"已批准" — 实际生产 profile 校验阶段
        # 已经强制 plan_mode_initial=True 必须有 readonly_tools 含 exit_plan_mode,
        # 而 plan_mode_initial=True 的 Runner 一定挂 PlanModeLifecycle.
        return {
            "ok": False,
            "warning": (
                "exit_plan_mode 直接执行了 (PlanModeLifecycle 未挂载?). "
                "当前 plan 已被记录但未触发 HITL 流程."
            ),
            "plan_markdown": plan,
        }


__all__ = ["ExitPlanMode"]
