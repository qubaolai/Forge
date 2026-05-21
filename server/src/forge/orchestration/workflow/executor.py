"""Workflow phase 执行器 (P2 尾项: 接入真实 chat 主链路)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from forge.agents.roles import get_agent_role, resolve_runtime_model_id
from forge.api.schemas.chat import ChatCompletionIn
from forge.chat import build_turn_orchestrator
from forge.chat.orchestrator import RunTurnOverrides
from forge.context.base import WorkflowContextLayer
from forge.prompts import get_registry
from forge.tools.base import Tool
from forge.tools.registry import ToolRegistry

from .artifact import Artifact
from .artifact_store import ArtifactStore
from .models import WorkflowPhaseState, WorkflowState


@dataclass
class PhaseExecutionResult:
    """phase 执行结果."""

    status: str  # done | partial | failed
    output: str = ""
    finish_reason: str = "stop"
    usage: dict[str, Any] = field(default_factory=dict)
    session_id: str | None = None
    message_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class WorkflowPhaseExecutor(Protocol):
    async def execute(
        self,
        *,
        state: WorkflowState,
        phase: WorkflowPhaseState,
        phase_index: int,
    ) -> PhaseExecutionResult: ...


class SyntheticPhaseExecutor:
    """占位执行器: 仅用于降级兜底."""

    async def execute(
        self,
        *,
        state: WorkflowState,
        phase: WorkflowPhaseState,
        phase_index: int,
    ) -> PhaseExecutionResult:
        _ = phase_index
        return PhaseExecutionResult(
            status="done",
            output=(
                f"[synthetic] role={phase.role} phase={phase.id} task={phase.task}; "
                f"input={state.input_message[:120]}"
            ),
            finish_reason="stop",
            metadata={"executor": "synthetic"},
        )


class ChatTurnPhaseExecutor:
    """通过现有 TurnOrchestrator.run_turn 执行 phase."""

    def __init__(
        self,
        *,
        user_id: str = "local",
        user_name: str = "Local Workflow",
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        self._user_id = user_id
        self._user_name = user_name
        self._artifact_store = artifact_store or ArtifactStore()
        # 复用同一 orchestrator, 避免每个 phase 反复构造依赖.
        self._orchestrator = build_turn_orchestrator()

    async def execute(
        self,
        *,
        state: WorkflowState,
        phase: WorkflowPhaseState,
        phase_index: int,
    ) -> PhaseExecutionResult:
        role = get_agent_role(phase.role)
        prompt = await _build_phase_prompt(
            state=state,
            phase=phase,
            phase_index=phase_index,
            artifact_store=self._artifact_store,
        )
        session_id = state.metadata.get("chat_session_id")
        body = ChatCompletionIn(
            message=prompt,
            session_id=session_id if isinstance(session_id, str) else None,
        )
        overrides = RunTurnOverrides(
            role=role.name,
            tools=_resolve_role_tools(role.allowed_tools),
            max_steps=role.max_steps,
            model_id=resolve_runtime_model_id(role),
            workspace_id=state.workspace_path,
            workflow_id=state.workflow_id,
            workflow_context=WorkflowContextLayer(
                workflow_id=state.workflow_id,
                template_id=state.template_id,
                mode=str(state.metadata.get("workflow_mode") or "light"),
                role_artifacts=_metadata_dict(state.metadata.get("role_artifacts")),
                recent_events=_recent_events(state),
            ),
            system_prompt=await _build_phase_system_prompt(
                state=state,
                phase=phase,
                phase_index=phase_index,
                artifact_store=self._artifact_store,
            ),
        )

        deltas: list[str] = []
        usage: dict[str, Any] = {}
        current_session_id: str | None = session_id if isinstance(session_id, str) else None
        message_id: str | None = None

        async for event in self._orchestrator.run_turn(
            user_id=self._user_id,
            user_name=self._user_name,
            body=body,
            trace_id=f"workflow:{state.workflow_id}:{phase.id}",
            overrides=overrides,
        ):
            payload = event.to_dict()
            et = payload.get("type")
            if et == "session_created":
                sid = payload.get("session_id")
                if isinstance(sid, str) and sid:
                    current_session_id = sid
            elif et == "message_start":
                mid = payload.get("message_id")
                if isinstance(mid, str) and mid:
                    message_id = mid
            elif et == "delta":
                deltas.append(str(payload.get("content") or ""))
            elif et == "done":
                usage = payload.get("usage") or {}
                return PhaseExecutionResult(
                    status="done",
                    output="".join(deltas),
                    finish_reason=str(payload.get("finish_reason") or "stop"),
                    usage=usage,
                    session_id=current_session_id,
                    message_id=message_id,
                    metadata={"executor": "chat_turn"},
                )
            elif et == "task_partial":
                usage = payload.get("usage") or {}
                return PhaseExecutionResult(
                    status="partial",
                    output=str(payload.get("content_so_far") or "".join(deltas)),
                    finish_reason=str(payload.get("reason") or "partial"),
                    usage=usage,
                    session_id=current_session_id,
                    message_id=message_id,
                    metadata={"executor": "chat_turn", "resumable": True},
                )
            elif et == "error":
                return PhaseExecutionResult(
                    status="failed",
                    output=str(payload.get("message") or "unknown error"),
                    finish_reason="error",
                    usage=usage,
                    session_id=current_session_id,
                    message_id=message_id,
                    metadata={"executor": "chat_turn"},
                )

        return PhaseExecutionResult(
            status="failed",
            output="phase execution ended without terminal event",
            finish_reason="error",
            usage=usage,
            session_id=current_session_id,
            message_id=message_id,
            metadata={"executor": "chat_turn"},
        )


class FallbackPhaseExecutor:
    """优先真实 chat 执行,失败降级 synthetic (P2 稳定性优先)."""

    def __init__(
        self,
        *,
        primary: WorkflowPhaseExecutor | None = None,
        fallback: WorkflowPhaseExecutor | None = None,
    ) -> None:
        self._primary = primary or ChatTurnPhaseExecutor()
        self._fallback = fallback or SyntheticPhaseExecutor()

    async def execute(
        self,
        *,
        state: WorkflowState,
        phase: WorkflowPhaseState,
        phase_index: int,
    ) -> PhaseExecutionResult:
        def _decorate_fallback(
            result: PhaseExecutionResult,
            *,
            reason: str,
            detail: str,
        ) -> PhaseExecutionResult:
            result.metadata["fallback_reason"] = reason
            result.metadata["fallback_detail"] = detail
            return result

        try:
            primary = await self._primary.execute(
                state=state,
                phase=phase,
                phase_index=phase_index,
            )
            if primary.status == "done":
                return primary
            fb = await self._fallback.execute(
                state=state,
                phase=phase,
                phase_index=phase_index,
            )
            return _decorate_fallback(
                fb,
                reason="primary_failed",
                detail=primary.output or primary.finish_reason,
            )
        except Exception as exc:  # noqa: BLE001
            fb = await self._fallback.execute(
                state=state,
                phase=phase,
                phase_index=phase_index,
            )
            return _decorate_fallback(
                fb,
                reason=type(exc).__name__,
                detail=str(exc),
            )


async def _build_phase_prompt(
    *,
    state: WorkflowState,
    phase: WorkflowPhaseState,
    phase_index: int,
    artifact_store: ArtifactStore | None = None,
) -> str:
    artifacts_text = await _format_completed_artifacts(
        state=state,
        artifact_store=artifact_store,
    )
    role_artifacts = _format_role_artifacts(state)

    return (
        "你在执行一个 workflow phase。\n"
        f"workflow_id={state.workflow_id}\n"
        f"template={state.template_id}\n"
        f"phase_index={phase_index}\n"
        f"phase_id={phase.id}\n"
        f"phase_role={phase.role}\n"
        f"phase_task={phase.task}\n\n"
        "原始用户需求:\n"
        f"{state.input_message}\n\n"
        "已完成阶段产出摘要:\n"
        f"{artifacts_text}\n\n"
        "按角色归档摘要:\n"
        f"{role_artifacts}\n\n"
        "请完成当前 phase，并给出可执行、可验证的结果。"
    )


async def _build_phase_system_prompt(
    *,
    state: WorkflowState,
    phase: WorkflowPhaseState,
    phase_index: int,
    artifact_store: ArtifactStore | None = None,
) -> str:
    role = get_agent_role(phase.role)
    return get_registry().render(
        role.prompt_template,
        user_system_prompt="",
        workflow_id=state.workflow_id,
        template_id=state.template_id,
        phase_index=phase_index,
        phase_id=phase.id,
        phase_role=phase.role,
        phase_task=phase.task,
        input_message=state.input_message,
        completed_artifacts=await _format_completed_artifacts(
            state=state,
            artifact_store=artifact_store,
        ),
        role_artifacts=_format_role_artifacts(state),
        # 父 agent 渲染 phase prompt 时该变量为空串; 仅 spawn_subagent 走 subagent
        # 渠道时才注入实际信道声明 (见 tools/builtin/agent/spawn.py:SUBAGENT_CHANNEL_NOTICE).
        subagent_channel_notice="",
    )


def _resolve_role_tools(allowed_tools: tuple[str, ...]) -> tuple[Tool, ...]:
    if not allowed_tools:
        return tuple(ToolRegistry.get_all())
    tools = []
    for name in allowed_tools:
        tool = ToolRegistry.get(name)
        if tool is None:
            continue
        tools.append(tool)
    return tuple(tools)


def _metadata_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _recent_events(state: WorkflowState) -> tuple[dict[str, Any], ...]:
    raw = state.metadata.get("recent_events")
    if not isinstance(raw, list):
        return ()
    return tuple(item for item in raw[-5:] if isinstance(item, dict))


async def _format_completed_artifacts(
    state: WorkflowState,
    *,
    artifact_store: ArtifactStore | None = None,
) -> str:
    completed = [p for p in state.phases if p.status == "done" and p.artifact_id]
    if not completed:
        return "(none)"
    store = artifact_store or ArtifactStore()
    workspace_root = Path(state.workspace_path).expanduser().resolve()
    lines: list[str] = []
    for idx, item in enumerate(completed[-8:], start=1):
        artifact = await store.load(
            item.artifact_id or "",
            workflow_id=state.workflow_id,
            workspace_root=workspace_root,
        )
        lines.append(_format_artifact_line(idx=idx, artifact=artifact, fallback_phase=item))
    return "\n".join(lines)


def _format_role_artifacts(state: WorkflowState) -> str:
    role_artifacts = state.metadata.get("role_artifacts")
    if not isinstance(role_artifacts, dict) or not role_artifacts:
        return "(none)"
    lines: list[str] = []
    for role, items in role_artifacts.items():
        if not isinstance(items, list) or not items:
            continue
        lines.append(f"- {role}:")
        for idx, item in enumerate(items[-5:], start=1):
            if not isinstance(item, dict):
                continue
            summary = str(item.get("summary") or "")
            phase_id = str(item.get("phase_id") or "-")
            lines.append(f"  {idx}. {phase_id}: {summary[:220]}")
    return "\n".join(lines) if lines else "(none)"


def _format_artifact_line(
    *,
    idx: int,
    artifact: Artifact | None,
    fallback_phase: WorkflowPhaseState,
) -> str:
    if artifact is None:
        return (
            f"{idx}. id={fallback_phase.artifact_id or '-'} "
            f"type=phase_output title={fallback_phase.role}:{fallback_phase.id} summary=(missing)"
        )
    return (
        f"{idx}. id={artifact.id} type={artifact.type.value} "
        f"title={artifact.title} summary={artifact.summary[:300]}"
    )
