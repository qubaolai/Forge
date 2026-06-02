"""WorkflowStepProvider: 工作流模式专用上下文提供者.

注入内容:
    - 当前 step 输入参数 (request.step_inputs)
    - 跨步骤共享 artifacts (request.workflow_context.role_artifacts)
    - 最近的 workflow events

无 IO, 全部从 request 字段构造.
"""

from __future__ import annotations

import json

from forge.context_mgmt.protocols import ContentProvider, TokenMeter
from forge.context_mgmt.types import ContentChunk, ContextRequest


class WorkflowStepProvider(ContentProvider):
    """工作流步骤上下文提供者."""

    def __init__(self, token_meter: TokenMeter) -> None:
        self._meter = token_meter

    @property
    def name(self) -> str:
        return "workflow_step"

    async def provide(self, request: ContextRequest) -> list[ContentChunk]:
        if request.step_id is None and not request.step_inputs:
            return []

        lines = ["## Workflow Step Context"]
        if request.step_id:
            lines.append(f"- step_id: {request.step_id}")
        if request.step_inputs:
            lines.append("### Step Inputs")
            lines.append(
                "```json\n"
                f"{json.dumps(request.step_inputs, ensure_ascii=False, sort_keys=True, indent=2)}\n"
                "```"
            )
        text = "\n".join(lines)
        return [
            ContentChunk(
                kind="workflow_step",
                layer="workflow_step",
                text=text,
                estimated_tokens=self._meter.count_text(text),
                message_count=1,
            )
        ]
