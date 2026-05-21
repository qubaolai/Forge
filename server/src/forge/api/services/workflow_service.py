"""Workflow 业务服务 (P2 alpha)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import Depends

from forge.core.exceptions import BadRequest, NotFound
from forge.orchestration.workflow import WorkflowOrchestrator
from forge.workspace.loader import load_workspace_context


class WorkflowService:
    def __init__(self, orchestrator: WorkflowOrchestrator | None = None) -> None:
        self._orchestrator = orchestrator or WorkflowOrchestrator()

    @property
    def orchestrator(self) -> WorkflowOrchestrator:
        return self._orchestrator

    async def start(
        self,
        *,
        message: str,
        template_id: str | None,
        workspace_path: str | None,
        pause_after_phase: bool,
        metadata: dict | None,
        owner_user_id: str = "local",
    ) -> dict:
        root = self._resolve_workspace_root(workspace_path)
        try:
            state = await self._orchestrator.start(
                message=message,
                workspace_root=root,
                template_id=template_id,
                pause_after_phase=pause_after_phase,
                metadata=metadata,
                owner_user_id=owner_user_id,
            )
        except KeyError as exc:
            raise BadRequest(str(exc), code=40041) from exc
        return state.to_dict()

    async def get(self, workflow_id: str, *, requester_user_id: str | None = None) -> dict:
        try:
            state = await self._orchestrator.get_state(
                workflow_id, requester_user_id=requester_user_id
            )
        except KeyError as exc:
            raise NotFound("workflow 不存在", code=40460) from exc
        return state.to_dict()

    async def resume(
        self,
        workflow_id: str,
        *,
        pause_after_phase: bool,
        requester_user_id: str | None = None,
    ) -> dict:
        try:
            state = await self._orchestrator.resume(
                workflow_id=workflow_id,
                pause_after_phase=pause_after_phase,
                requester_user_id=requester_user_id,
            )
        except KeyError as exc:
            raise NotFound("workflow 不存在", code=40460) from exc
        return state.to_dict()

    async def abort(
        self,
        workflow_id: str,
        *,
        reason: str | None,
        requester_user_id: str | None = None,
    ) -> dict:
        try:
            state = await self._orchestrator.abort(
                workflow_id=workflow_id,
                reason=reason,
                requester_user_id=requester_user_id,
            )
        except KeyError as exc:
            raise NotFound("workflow 不存在", code=40460) from exc
        return state.to_dict()

    async def events(
        self,
        workflow_id: str,
        *,
        from_event_id: str | None,
        requester_user_id: str | None = None,
    ) -> list[dict]:
        try:
            events = await self._orchestrator.list_events(
                workflow_id=workflow_id,
                from_event_id=from_event_id,
                requester_user_id=requester_user_id,
            )
        except KeyError as exc:
            raise NotFound("workflow 不存在", code=40460) from exc
        return [evt.to_dict() for evt in events]

    @staticmethod
    def _resolve_workspace_root(workspace_path: str | None) -> Path:
        if workspace_path:
            p = Path(workspace_path).expanduser().resolve()
            if not p.exists():
                raise BadRequest("workspace_path 不存在", code=40042)
            if not p.is_dir():
                raise BadRequest("workspace_path 不是目录", code=40043)
            return p
        ctx = load_workspace_context()
        return ctx.root_path


_WORKFLOW_SERVICE: WorkflowService | None = None


def get_workflow_service() -> WorkflowService:
    """模块级单例 — orchestrator 内部维护 active 状态,
    跨请求必须共享同一个实例, 否则 abort/resume 看不到 start 装载的 state."""
    global _WORKFLOW_SERVICE
    if _WORKFLOW_SERVICE is None:
        _WORKFLOW_SERVICE = WorkflowService()
    return _WORKFLOW_SERVICE


def reset_workflow_service() -> None:
    """单测用: 清掉单例 + orchestrator, 下次 get_workflow_service() 重建."""
    global _WORKFLOW_SERVICE
    _WORKFLOW_SERVICE = None


WorkflowServiceDep = Annotated[WorkflowService, Depends(get_workflow_service)]
