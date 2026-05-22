"""对话接口 (SSE 流式).

前端协议 (与 web/src/types/index.ts SSEEvent 对齐):
    每个事件一行 JSON, 用 data: 包裹, 事件以双换行分隔.

    {"type": "message_start",   "message_id": "...", "session_id": "..."}
    {"type": "session_created", "session_id": "...", "title": "..."}
    {"type": "session_renamed", "session_id": "...", "title": "..."}
    {"type": "tool_call",       "tool_call": {...}}
    {"type": "tool_result",     "tool_call_id": "...", "result": "...", "status": "success"|"error"}
    {"type": "delta",           "content": "..."}
    {"type": "reasoning_delta", "content": "..."}
    {"type": "reasoning_end"    }
    {"type": "citations",       "citations": [...]}
    {"type": "done",            "usage": {...}, "finish_reason": "stop"|"length"|"tool_calls"|"aborted"}
    {"type": "error",           "message": "...", "code": "..."}

路由决策（ModeRouter，M1 实现）:
    - mode="chat"                → TurnOrchestrator (当前唯一实现)
    - mode="auto" (默认)         → 同 chat，M1 后由 ModeRouter 智能路由
    - mode="task"                → AdaptiveRunOrchestrator (M3+ 实现)
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from config.settings import get_settings
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from forge.adaptive.mode_router import ModeRouter
from forge.adaptive.models import AdaptiveRun, RunStatus
from forge.adaptive.options import TaskOptions
from forge.adaptive.store import AdaptiveRunStore
from forge.adaptive.supervisor import build_orchestrator_factory, get_run_supervisor
from forge.api.dependencies import CurrentUser
from forge.api.middleware.client_type import set_client_type
from forge.api.schemas.chat import (
    ChatCompletionIn,
    ChatRegenerateIn,
    ChatResumeIn,
    ChatStopIn,
)
from forge.chat import build_turn_orchestrator, get_active_streams
from forge.core.exceptions import NotFound
from forge.core.response import success
from forge.infrastructure.database.database import get_session_factory
from forge.infrastructure.storage import make_message_store, make_session_store
from forge.quota import UserQuotaExceeded, get_usage_quota_manager
from forge.utils.id_generator import new_id

logger = logging.getLogger(__name__)

router = APIRouter()
mode_router = ModeRouter()


def _sse(event: dict) -> bytes:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()


def _resolve_workspace_path(raw_workspace_path: str | None) -> str:
    text = (raw_workspace_path or "").strip()
    if not text:
        return str(Path.cwd())
    return str(Path(text).expanduser().resolve())


@router.post("/completions")
async def chat_completions(
    body: ChatCompletionIn,
    user: CurrentUser,
    request: Request,
):
    """流式 chat / task 统一入口.

    当前行为:
      - mode="chat" | "auto" → TurnOrchestrator（聊天路径，零退化）
      - mode="task"           → AdaptiveRunOrchestrator（M5 串行执行）
    """
    trace_id = getattr(request.state, "trace_id", "")
    client_type = getattr(request.state, "client_type", "cli")

    decision = mode_router.decide(mode=body.mode, message=body.message, task_options=body.task_options)
    logger.info("ModeRouter 决策 target=%s reason=%s", decision.target, decision.reason)

    if decision.target == "adaptive":
        # adaptive 路径也要走用量配额，避免任何用户绕过 quota 无限消耗 LLM
        try:
            get_usage_quota_manager().check_sync(user.id)
        except UserQuotaExceeded as exc:
            # 在闭包外提前取出 status，避免 generator 内引用 except 已清理的局部变量
            quota_status = exc.status.to_dict() if hasattr(exc, "status") else None

            async def _quota_denied():
                yield _sse(
                    {
                        "type": "error",
                        "message": "已超出 LLM 用量额度，请稍后再试",
                        "code": "quota_exceeded",
                        "status": quota_status,
                    }
                )

            return StreamingResponse(
                _quota_denied(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        settings = get_settings()
        options = TaskOptions.build(body.task_options, settings=settings)
        # adaptive 入口必须显式指定 workspace_path，避免误用 server 进程 cwd
        if not options.workspace_path:
            async def _missing_workspace():
                yield _sse(
                    {
                        "type": "error",
                        "message": "缺少 workspace_path：请在 task_options.workspace_path 中显式指定项目路径",
                        "code": "missing_workspace_path",
                    }
                )

            return StreamingResponse(
                _missing_workspace(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        workspace_path = _resolve_workspace_path(options.workspace_path)
        store = AdaptiveRunStore(workspace_path=workspace_path)
        run = AdaptiveRun(
            run_id=new_id("run"),
            workspace_path=workspace_path,
            goal=body.message,
            status=RunStatus.CREATED,
            owner_user_id=user.id,
            metadata={
                "source": "api:/chat/completions",
                "requested_mode": body.mode,
                "route_target": decision.target,
            },
            options_snapshot=options.to_dict(),
        )
        await store.save_run(run)

        event_queue: asyncio.Queue[dict] = asyncio.Queue()

        async def _on_event(evt: dict) -> None:
            await event_queue.put(evt)

        task_execution_cfg = getattr(settings, "task_execution", None)
        allowlist = list(getattr(task_execution_cfg, "tool_allowlist", []))
        factory = build_orchestrator_factory(
            tool_allowlist=allowlist,
            event_handler=_on_event,
        )
        # B5: 走 supervisor 投递后台任务，保证 abort/decide 能取消同一 task
        run_task = await get_run_supervisor().start_run(
            run=run,
            options=options,
            store=store,
            orchestrator_factory=factory,
        )

        async def _task_stream():
            """统一异常保护：任何阶段出错都用 error 事件结束流，并确保 run.done 至少推一次。"""
            set_client_type(client_type)
            done = False
            run_exception: BaseException | None = None
            try:
                while True:
                    if done and event_queue.empty():
                        break
                    try:
                        evt = await asyncio.wait_for(event_queue.get(), timeout=0.2)
                        yield _sse(
                            {
                                "type": evt.get("type", ""),
                                "run_id": evt.get("run_id", run.run_id),
                                "event_id": evt.get("id", ""),
                                "ts": evt.get("ts", ""),
                                "payload": evt.get("payload", {}),
                            }
                        )
                        continue
                    except TimeoutError:
                        pass

                    if run_task.done():
                        done = True
                        if run_task.exception() is not None:
                            # 记录异常但不立即 break，先把 queue 里剩余事件吐完
                            run_exception = run_task.exception()
            finally:
                if not run_task.done():
                    run_task.cancel()
                    try:
                        await run_task
                    except (asyncio.CancelledError, Exception) as exc:  # noqa: BLE001
                        if run_exception is None:
                            run_exception = exc

            if run_exception is not None:
                logger.exception("adaptive run 异常 run_id=%s", run.run_id, exc_info=run_exception)
                yield _sse(
                    {
                        "type": "error",
                        "run_id": run.run_id,
                        "message": str(run_exception),
                        "code": "adaptive_failed",
                    }
                )
                yield _sse(
                    {
                        "type": "run.done",
                        "run_id": run.run_id,
                        "status": RunStatus.FAILED.value,
                        "artifact_ids": list(run.artifact_ids),
                    }
                )
                return

            # 正常完成路径：从 task 结果取最新 run 状态
            result_run = run_task.result()
            yield _sse(
                {
                    "type": "run.done",
                    "run_id": result_run.run_id,
                    "status": result_run.status.value,
                    "artifact_ids": list(result_run.artifact_ids),
                }
            )

        return StreamingResponse(
            _task_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    # B11/P2-11: chat 路径走 TurnOrchestrator；simple 路径已删除（mode_router 不再产出）
    orchestrator = build_turn_orchestrator()

    async def event_stream():
        set_client_type(client_type)
        async for event in orchestrator.run_turn(
            user_id=user.id,
            user_name=user.name,
            body=body,
            trace_id=trace_id,
        ):
            yield _sse(event.to_dict())

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/quota")
async def chat_quota(user: CurrentUser):
    """查看当前用户滚动 LLM 用量额度."""
    status = await get_usage_quota_manager().status(user.id)
    return success(status.to_dict())


@router.post("/resume")
async def chat_resume(body: ChatResumeIn, user: CurrentUser, request: Request):
    """继续未完成的 assistant 消息 (status=aborted / partial)."""
    trace_id = getattr(request.state, "trace_id", "")
    client_type = getattr(request.state, "client_type", "cli")
    orchestrator = build_turn_orchestrator()

    async def event_stream():
        set_client_type(client_type)
        async for event in orchestrator.resume_turn(
            user_id=user.id,
            message_id=body.message_id,
            trace_id=trace_id,
        ):
            yield _sse(event.to_dict())

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/stop")
async def chat_stop(body: ChatStopIn, user: CurrentUser):
    """中断指定消息的流式生成."""
    streams = get_active_streams()
    event = streams.get(body.message_id)
    if event is None:
        return success(None)
    event.set()
    return success(None)


@router.post("/regenerate")
async def chat_regenerate(body: ChatRegenerateIn, user: CurrentUser):
    """重新生成 assistant 消息."""
    factory = get_session_factory()
    async with factory() as db:
        repo = make_message_store(db)
        sess_repo = make_session_store(db)
        asst = await repo.get_by_id(body.message_id)
        if not asst or asst.role != "assistant":
            raise NotFound("消息不存在", code=40440)

        session = await sess_repo.get_by_id(asst.session_id)
        if not session:
            raise NotFound("会话不存在", code=40410)

        parent_id = asst.parent_id
        if not parent_id:
            raise NotFound("找不到原始用户消息", code=40441)
        parent = await repo.get_by_id(parent_id)
        if not parent:
            raise NotFound("原始用户消息已删除", code=40441)

        await repo.delete_by_id(asst.id)

        return success(
            {
                "session_id": session.id,
                "user_message": parent.content,
                "new_message_id": new_id("msg"),
            }
        )
