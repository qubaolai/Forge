"""对话接口 (SSE 流式) — 纯聊天服务。

与 web/src/types/index.ts SSEEvent 对齐:
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

任务执行请走 POST /api/v1/runs（Adaptive 7 步流程）。
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

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
from forge.quota import get_usage_quota_manager
from forge.utils.id_generator import new_id

logger = logging.getLogger(__name__)

router = APIRouter()


def _sse(event: dict) -> bytes:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()


@router.post("/completions")
async def chat_completions(
    body: ChatCompletionIn,
    user: CurrentUser,
    request: Request,
):
    """流式聊天对话 — 纯 ReAct + 查询类工具。"""
    trace_id = getattr(request.state, "trace_id", "")
    client_type = getattr(request.state, "client_type", "cli")

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
