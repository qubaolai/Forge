"""新版 /v1/runs 路由 (Profile 驱动, RunStore 持久化).

服务 CLI 端 (plan_exec / workflow). chat 端走 /v1/chat/*.

端点:
    POST /v1/runs                       新建并启动 run
    GET  /v1/runs                       列出当前用户的 run (admin 全见)
    GET  /v1/runs/{id}                  查询 run
    GET  /v1/runs/{id}/events           SSE 订阅事件流 (cursor 模式)
    POST /v1/runs/{id}/abort            中止 run

注: HITL 决策走 POST /v1/decisions/{token} (与 workflow_gate 通用), 不再走
/v1/runs/{id}/decide. workflow_gate 等 HITL 事件在 events.jsonl 内携带 token,
客户端按 token 走通用 decisions 路由.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from forge.agents.profiles import get_agent_profile, list_modes
from forge.agents.run_orchestrator import RunOrchestrator
from forge.agents.run_supervisor import get_run_supervisor
from forge.api.dependencies import AuthenticatedUser
from forge.core.exceptions import BadRequest, Conflict, Forbidden, NotFound
from forge.core.response import success
from forge.infrastructure.run_store import RunStore
from forge.infrastructure.run_store_models import (
    TERMINAL_STATUSES,
    RunRecord,
)

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/runs", tags=["runs"])


# ---------------------------------------------------------------------------
# request / response models
# ---------------------------------------------------------------------------
class RunCreateIn(BaseModel):
    """POST /v1/runs 入参."""

    mode: str = Field(..., description="agent_mode (plan_exec / workflow / ...)")
    goal: str = Field(..., min_length=1, description="任务目标 (用户自然语言)")
    workspace_path: str = Field(
        ...,
        min_length=1,
        description="工作区根路径 (绝对路径). RunStore 落在 <workspace>/.forge/runs/",
    )
    user_system_prompt: str = Field(
        default="",
        description="用户附加的 system 约束 (拼到 profile 模板)",
    )
    model_options: dict | None = Field(
        default=None,
        description="覆盖 profile 默认模型: {provider, model}",
    )
    workflow_template: dict | None = Field(
        default=None,
        description="workflow 模式专用: YAML 模板内容 (本阶段未启用, 占位)",
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _sse(event: dict) -> bytes:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()


def _resolve_workspace_path(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise BadRequest("workspace_path 必填, 不能为空", code=40051)
    return str(Path(text).expanduser().resolve())


def _ensure_owner(record: RunRecord, user) -> None:
    if record.owner_user_id == user.user_id:
        return
    if getattr(user, "role", "") in ("owner", "admin"):
        return
    raise Forbidden("无权访问该 run", code=40330)


def _record_to_dict(record: RunRecord) -> dict:
    return record.to_dict()


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------
@router.post("")
async def create_run(body: RunCreateIn, user: AuthenticatedUser):
    """创建 run 并立即启动后台执行."""
    # 1. profile 校验
    if body.mode not in list_modes():
        raise BadRequest(
            f"未知 mode={body.mode!r}, 可选: {list_modes()}",
            code=40052,
        )
    profile = get_agent_profile(body.mode)
    if profile.persistence != "run_store":
        # chat 模式不允许走 /runs (chat 走 /v1/chat/*)
        raise BadRequest(
            f"mode={body.mode!r} 的 persistence={profile.persistence}, 不应走 /v1/runs",
            code=40053,
        )
    if profile.requires_template and not body.workflow_template:
        raise BadRequest(
            f"mode={body.mode!r} 需要 workflow_template, 但未提供",
            code=40054,
        )
    if body.workflow_template is not None:
        # 启动期格式校验, 错误模板早抛
        from forge.agents.workflow_lifecycle import (
            WorkflowTemplateError,
            validate_workflow_template,
        )
        try:
            validate_workflow_template(body.workflow_template)
        except WorkflowTemplateError as exc:
            raise BadRequest(
                f"workflow_template 校验失败: {exc}", code=40056,
            ) from exc

    # 2. 落档案
    workspace_path = _resolve_workspace_path(body.workspace_path)
    store = RunStore(workspace_path=workspace_path)
    metadata: dict = {"source": "api:/v1/runs"}
    if body.workflow_template:
        metadata["workflow_template"] = body.workflow_template

    record = await store.create_run(
        mode=body.mode,
        goal=body.goal,
        owner_user_id=user.user_id,
        metadata=metadata,
    )

    # 3. 投递后台执行
    orchestrator = RunOrchestrator(
        record=record,
        store=store,
        profile=profile,
        goal=body.goal,
        user_id=user.user_id,
        user_system_prompt=body.user_system_prompt,
        model_options=body.model_options or {},
        workflow_template=body.workflow_template,
    )
    get_run_supervisor().register(orchestrator)
    logger.info(
        "Run 创建并投递: run=%s mode=%s workspace=%s user=%s",
        record.run_id, body.mode, workspace_path, user.user_id,
    )
    return success(_record_to_dict(record))


@router.get("")
async def list_runs(
    user: AuthenticatedUser,
    workspace_path: str = Query(..., description="工作区根路径"),
    mode: str | None = Query(default=None, description="按 mode 过滤"),
    limit: int = Query(100, ge=1, le=500),
):
    """列出指定 workspace 下当前用户的 run. admin 可见所有 owner."""
    resolved = _resolve_workspace_path(workspace_path)
    store = RunStore(workspace_path=resolved)
    owner_filter = (
        None if getattr(user, "role", "") in ("owner", "admin") else user.user_id
    )
    items = await store.list_runs(mode=mode, owner_user_id=owner_filter, limit=limit)
    return success({"items": [_record_to_dict(r) for r in items], "total": len(items)})


@router.get("/{run_id}")
async def get_run(
    run_id: str,
    user: AuthenticatedUser,
    workspace_path: str = Query(..., description="工作区根路径"),
):
    resolved = _resolve_workspace_path(workspace_path)
    store = RunStore(workspace_path=resolved)
    record = await store.load_run(run_id)
    if record is None:
        raise NotFound("run 不存在", code=40450)
    _ensure_owner(record, user)
    return success(_record_to_dict(record))


@router.get("/{run_id}/events")
async def stream_run_events(
    run_id: str,
    request: Request,
    user: AuthenticatedUser,
    workspace_path: str = Query(..., description="工作区根路径"),
    after_event_id: str | None = Query(default=None),
    follow: bool = Query(default=False),
    poll_interval_ms: int = Query(1000, ge=200, le=5000),
):
    """SSE 订阅事件流. follow=true 在 run 未到终态时持续轮询."""
    resolved = _resolve_workspace_path(workspace_path)
    store = RunStore(workspace_path=resolved)
    record = await store.load_run(run_id)
    if record is None:
        raise NotFound("run 不存在", code=40450)
    _ensure_owner(record, user)
    if after_event_id and not await store.has_event(run_id, after_event_id):
        raise BadRequest(
            f"after_event_id={after_event_id} 不存在; 客户端缓存可能过期, "
            "请改用 follow=true&after_event_id=空 重新订阅",
            code=40055,
        )

    async def _stream():
        cursor = after_event_id
        sent: set[str] = set()
        while True:
            rows = await store.list_events(run_id, after_event_id=cursor)
            for row in rows:
                if row.id in sent:
                    continue
                sent.add(row.id)
                cursor = row.id
                yield _sse(row.to_dict())
            if not follow:
                break
            if await request.is_disconnected():
                break
            latest = await store.load_run(run_id)
            if latest is not None and latest.status in TERMINAL_STATUSES:
                # 终态后再吐一次保证不漏事件, 然后关闭流
                final_rows = await store.list_events(run_id, after_event_id=cursor)
                for row in final_rows:
                    if row.id in sent:
                        continue
                    sent.add(row.id)
                    yield _sse(row.to_dict())
                yield _sse({
                    "type": "stream.closed",
                    "run_id": run_id,
                    "status": latest.status,
                    "reason": "terminal_state",
                })
                break
            await asyncio.sleep(poll_interval_ms / 1000)

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/{run_id}/abort")
async def abort_run(
    run_id: str,
    user: AuthenticatedUser,
    workspace_path: str = Query(..., description="工作区根路径"),
):
    resolved = _resolve_workspace_path(workspace_path)
    store = RunStore(workspace_path=resolved)
    record = await store.load_run(run_id)
    if record is None:
        raise NotFound("run 不存在", code=40450)
    _ensure_owner(record, user)
    if record.status in TERMINAL_STATUSES:
        raise Conflict(
            f"run 已处于终态 {record.status}, 无法中止", code=40950,
        )

    cancelled = await get_run_supervisor().cancel(run_id)
    # supervisor 内会 transition_status -> aborted; 重新加载
    record = await store.load_run(run_id)
    if not cancelled and record is not None and record.status not in TERMINAL_STATUSES:
        # supervisor 不持有 task (例如进程重启), 直接落 aborted
        record = await store.transition_status(
            run_id, "aborted", payload={"reason": "user_request"},
        )
    return success(_record_to_dict(record) if record else None)


__all__ = ["router"]
