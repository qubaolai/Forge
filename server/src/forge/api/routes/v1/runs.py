"""AdaptiveRun 路由骨架（M3）。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from config.settings import get_settings
from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from forge.adaptive import events, run_index
from forge.adaptive.models import AdaptiveRun, RunStatus
from forge.adaptive.options import TaskOptions
from forge.adaptive.store import AdaptiveRunStore
from forge.adaptive.supervisor import build_orchestrator_factory, get_run_supervisor
from forge.api.dependencies import CurrentUser
from forge.api.schemas.run import DecideIn, RunCreateIn
from forge.core.exceptions import BadRequest, Conflict, Forbidden, NotFound
from forge.core.response import success
from forge.utils.id_generator import new_id

router = APIRouter(prefix="/runs", tags=["runs"])


def _sse(event: dict) -> bytes:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()


def _resolve_workspace_path(raw_workspace_path: str | None) -> str:
    text = (raw_workspace_path or "").strip()
    if not text:
        return str(Path.cwd())
    return str(Path(text).expanduser().resolve())


async def _resolve_workspace_for_run(run_id: str, raw_workspace_path: str | None) -> str:
    """C11/D6: 查询型端点解析 workspace_path —— 优先客户端传入，
    否则用全局 run index 反查；都没有再退化到 cwd。"""
    if (raw_workspace_path or "").strip():
        return _resolve_workspace_path(raw_workspace_path)
    entry = await run_index.lookup(run_id)
    if entry is not None and entry.workspace_path:
        return entry.workspace_path
    return _resolve_workspace_path(None)


def _run_to_dict(run: AdaptiveRun) -> dict:
    return run.to_dict()


def _ensure_owner(run: AdaptiveRun, user) -> None:
    """校验 run 归属。非 owner 也非 admin 直接拒绝（多租户隔离）。"""
    if run.owner_user_id == user.id:
        return
    if getattr(user, "role", "") in ("owner", "admin"):
        return
    raise Forbidden("无权访问该 run", code=40330)


@router.post("")
async def create_run(body: RunCreateIn, user: CurrentUser):
    """创建 run（仅建档，不触发执行）。"""
    settings = get_settings()
    options = TaskOptions.build(body.task_options, settings=settings)
    # 创建型操作必须显式指定 workspace_path，避免落到 server 进程 cwd
    if not options.workspace_path:
        raise BadRequest(
            "缺少 workspace_path：请在 task_options.workspace_path 中显式指定项目路径",
            code=40052,
        )
    workspace_path = _resolve_workspace_path(options.workspace_path)
    store = AdaptiveRunStore(workspace_path=workspace_path)

    run = AdaptiveRun(
        run_id=new_id("run"),
        workspace_path=workspace_path,
        goal=body.goal,
        owner_user_id=user.id,
        status=RunStatus.CREATED,
        metadata={"source": "api:/runs"},
        options_snapshot=options.to_dict(),
    )
    await store.save_run(run)
    # C11/D6: 写全局 run index，让查询 API 不依赖客户端持续传 workspace_path
    await run_index.record_run(
        run_id=run.run_id,
        owner_user_id=user.id,
        workspace_path=workspace_path,
    )
    await store.append_event(
        run.run_id,
        events.RUN_CREATED,
        {
            "status": run.status.value,
            "goal": run.goal,
            "owner_user_id": user.id,
        },
    )

    # B5/P0-3: POST /runs 不再只建档，投递后台任务真正执行 7 步主流程；
    # 客户端可通过 GET /runs/{id}/events?follow=true 订阅 SSE 跟进。
    allowlist = list(getattr(getattr(settings, "task_execution", None), "tool_allowlist", []))
    factory = build_orchestrator_factory(tool_allowlist=allowlist)
    await get_run_supervisor().start_run(
        run=run,
        options=options,
        store=store,
        orchestrator_factory=factory,
    )
    return success(_run_to_dict(run))


@router.get("")
async def list_runs(
    user: CurrentUser,
    workspace_path: str | None = None,
    status: RunStatus | None = None,
    limit: int = Query(100, ge=1, le=500),
):
    """列出 run（仅当前用户拥有的）。管理员可见所有。"""
    store = AdaptiveRunStore(workspace_path=_resolve_workspace_path(workspace_path))
    owner_filter = None if getattr(user, "role", "") in ("owner", "admin") else user.id
    runs = await store.list_runs(limit=limit, owner_user_id=owner_filter)
    if status is not None:
        runs = [run for run in runs if run.status == status]
    payload = [_run_to_dict(run) for run in runs]
    return success({"items": payload, "total": len(payload)})


@router.get("/{run_id}")
async def get_run(run_id: str, user: CurrentUser, workspace_path: str | None = None):
    """查询单个 run。非 owner 也非 admin 直接 403。"""
    # C11/D6: 客户端可省略 workspace_path，由全局 index 反查
    resolved = await _resolve_workspace_for_run(run_id, workspace_path)
    store = AdaptiveRunStore(workspace_path=resolved)
    run = await store.load_run(run_id)
    if run is None:
        raise NotFound("run 不存在", code=40450)
    _ensure_owner(run, user)
    return success(_run_to_dict(run))


@router.get("/{run_id}/events")
async def stream_run_events(
    run_id: str,
    request: Request,
    user: CurrentUser,
    workspace_path: str | None = None,
    after_event_id: str | None = None,
    follow: bool = False,
    poll_interval_ms: int = Query(1000, ge=200, le=5000),
):
    """读取 run 事件流（SSE）。"""
    # C11/D6: 缺省 workspace_path 时走全局 index 反查
    resolved = await _resolve_workspace_for_run(run_id, workspace_path)
    store = AdaptiveRunStore(workspace_path=resolved)
    run = await store.load_run(run_id)
    if run is None:
        raise NotFound("run 不存在", code=40450)
    _ensure_owner(run, user)
    # B4/P1-8: cursor 不存在则 400，避免 follow 模式陷入静默空轮询
    if after_event_id and not await store.has_event(run_id, after_event_id):
        raise BadRequest(
            f"after_event_id={after_event_id} 不存在；客户端缓存可能过期，请改用 follow=true&after_event_id=空 重新订阅",
            code=40053,
        )

    terminal_statuses = {
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.ABORTED,
        RunStatus.BLOCKED,
    }

    async def _stream():
        cursor = after_event_id
        sent_ids: set[str] = set()
        while True:
            rows = await store.list_events(run_id, after_event_id=cursor)
            for row in rows:
                if row.id in sent_ids:
                    continue
                sent_ids.add(row.id)
                cursor = row.id
                yield _sse(row.to_dict())
            if not follow:
                break
            if await request.is_disconnected():
                break
            # follow 模式：若 run 已到终态，再轮询一次确保事件吐完即退出，
            # 避免对已结束 run 永远空轮询
            latest = await store.load_run(run_id)
            if latest is not None and latest.status in terminal_statuses:
                final_rows = await store.list_events(run_id, after_event_id=cursor)
                for row in final_rows:
                    if row.id in sent_ids:
                        continue
                    sent_ids.add(row.id)
                    yield _sse(row.to_dict())
                yield _sse(
                    {
                        "type": "stream.closed",
                        "run_id": run_id,
                        "status": latest.status.value,
                        "reason": "terminal_state",
                    }
                )
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
async def abort_run(run_id: str, user: CurrentUser, workspace_path: str | None = None):
    """中止 run（同时取消后台执行任务）。"""
    resolved = await _resolve_workspace_for_run(run_id, workspace_path)
    store = AdaptiveRunStore(workspace_path=resolved)
    run = await store.load_run(run_id)
    if run is None:
        raise NotFound("run 不存在", code=40450)
    _ensure_owner(run, user)
    # B5: 先取消后台 task（若仍在跑），supervisor 内部 CancelledError 会把状态推到 ABORTED；
    # 若 supervisor 未持有 task（重启后），下面 transition_status 直接落终态。
    cancelled = await get_run_supervisor().cancel_run(run_id)
    if cancelled:
        # supervisor lifecycle 已把 status 推到 ABORTED，重新加载一次即可
        updated = await store.load_run(run_id)
        return success(_run_to_dict(updated)) if updated else success(None)
    try:
        updated = await store.transition_status(
            run_id,
            RunStatus.ABORTED,
            payload={"reason": "user_request"},
        )
    except ValueError as exc:
        raise Conflict(f"无法中止当前 run: {exc}", code=40950) from exc
    return success(_run_to_dict(updated))


@router.post("/{run_id}/decide")
async def decide_run(run_id: str, body: DecideIn, user: CurrentUser, workspace_path: str | None = None):
    """处理 BLOCKED run 的人工决策 (HITL)。

    - ``continue`` → 把 run 状态切回 PLANNING，并由 supervisor 重新唤起
      orchestrator 从规划开始继续执行（discovery 复用历史结果）。
    - ``abort``    → 取消后台 task 并落 ABORTED。
    """
    resolved = await _resolve_workspace_for_run(run_id, workspace_path)
    store = AdaptiveRunStore(workspace_path=resolved)
    run = await store.load_run(run_id)
    if run is None:
        raise NotFound("run 不存在", code=40450)
    _ensure_owner(run, user)
    if run.status != RunStatus.BLOCKED:
        raise BadRequest("当前 run 不是 BLOCKED 状态", code=40051)

    if body.decision == "abort":
        await get_run_supervisor().cancel_run(run_id)
        try:
            run = await store.transition_status(
                run_id,
                RunStatus.ABORTED,
                payload={"decision": "abort", "notes": body.notes or ""},
            )
        except ValueError as exc:
            raise Conflict(f"非法状态转换: {exc}", code=40951) from exc
        return success(_run_to_dict(run))

    # continue: 状态推到 PLANNING + 重新启动后台 task
    try:
        run = await store.transition_status(
            run_id,
            RunStatus.PLANNING,
            payload={"decision": "continue", "notes": body.notes or ""},
        )
    except ValueError as exc:
        raise Conflict(f"非法状态转换: {exc}", code=40951) from exc

    # B5/HITL: 用持久化 options_snapshot 重建 TaskOptions，确保跨进程恢复
    options = TaskOptions.from_dict(run.options_snapshot)
    if not options.workspace_path:
        # 历史 run 可能没存 workspace_path，回退到 run.workspace_path
        options_dict = options.to_dict()
        options_dict["workspace_path"] = run.workspace_path
        options = TaskOptions.from_dict(options_dict)

    settings = get_settings()
    allowlist = list(getattr(getattr(settings, "task_execution", None), "tool_allowlist", []))
    factory = build_orchestrator_factory(tool_allowlist=allowlist)
    await get_run_supervisor().resume_run(
        run=run,
        options=options,
        store=store,
        orchestrator_factory=factory,
    )
    return success(_run_to_dict(run))
