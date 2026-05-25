"""WorkspaceProvider: 从 ContextRequest 中提取 workspace / workflow / project / role 信息.

无 IO, 纯从 request 字段构造 ContentChunk.
等价于 CompositeContextBuilder._compose_system_message() 中
workspace / project_decisions / workflow / role_history 部分的拼装逻辑.
"""

from __future__ import annotations

import json

from forge.context_mgmt.protocols import TokenMeter
from forge.context_mgmt.types import ContentChunk, ContextRequest


class WorkspaceProvider:
    """无 IO, 从 request 直接组装层叠上下文文本."""

    def __init__(self, token_meter: TokenMeter) -> None:
        self._meter = token_meter

    @property
    def name(self) -> str:
        return "workspace"

    async def provide(self, request: ContextRequest) -> list[ContentChunk]:
        chunks: list[ContentChunk] = []

        ws_text = self._render_workspace(request)
        if ws_text:
            chunks.append(
                ContentChunk(
                    kind="workspace",
                    layer="workspace",
                    text=ws_text,
                    estimated_tokens=self._meter.count_text(ws_text),
                )
            )

        decisions_text = self._render_project_decisions(request)
        if decisions_text:
            chunks.append(
                ContentChunk(
                    kind="workspace",
                    layer="workspace",
                    text=decisions_text,
                    estimated_tokens=self._meter.count_text(decisions_text),
                )
            )

        wf_text = self._render_workflow(request)
        if wf_text:
            chunks.append(
                ContentChunk(
                    kind="workspace",
                    layer="workspace",
                    text=wf_text,
                    estimated_tokens=self._meter.count_text(wf_text),
                )
            )

        role_text = self._render_role_history(request)
        if role_text:
            chunks.append(
                ContentChunk(
                    kind="workspace",
                    layer="workspace",
                    text=role_text,
                    estimated_tokens=self._meter.count_text(role_text),
                )
            )

        return chunks

    @staticmethod
    def _render_workspace(request: ContextRequest) -> str:
        ws = request.workspace_context
        if ws is None:
            return ""
        parts = [
            "## Workspace Context",
            f"- workspace_id: {ws.workspace_id}",
            f"- root_path: {ws.root_path}",
        ]
        if ws.assistant_prompt.strip():
            parts.append("### ASSISTANT.md\n" + ws.assistant_prompt.strip())
        if ws.settings:
            parts.append(
                "### Settings\n"
                "```json\n"
                f"{json.dumps(ws.settings, ensure_ascii=False, sort_keys=True)}\n"
                "```"
            )
        return "\n".join(parts)

    @staticmethod
    def _render_project_decisions(request: ContextRequest) -> str:
        if not request.project_decisions:
            return ""
        lines = ["## Project Decisions"]
        lines.extend(f"- {item}" for item in request.project_decisions if item.strip())
        return "\n".join(lines) if len(lines) > 1 else ""

    @staticmethod
    def _render_workflow(request: ContextRequest) -> str:
        wf = request.workflow_context
        if wf is None:
            return ""
        lines = [
            "## Workflow Context",
            f"- workflow_id: {wf.workflow_id}",
            f"- template_id: {wf.template_id}",
            f"- mode: {wf.mode}",
        ]
        if wf.role_artifacts:
            lines.append("### Role Artifacts")
            for role, items in wf.role_artifacts.items():
                lines.append(f"- {role}: {items}")
        if wf.recent_events:
            lines.append("### Recent Events")
            for event in wf.recent_events[-5:]:
                event_type = event.get("type", "-")
                payload = event.get("payload", {})
                lines.append(f"- {event_type}: {payload}")
        return "\n".join(lines)

    @staticmethod
    def _render_role_history(request: ContextRequest) -> str:
        if not request.role_history:
            return ""
        lines = ["## Role History"]
        lines.extend(f"- {item}" for item in request.role_history if item.strip())
        return "\n".join(lines) if len(lines) > 1 else ""
