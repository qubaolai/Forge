"""对话接口 (SSE 流式).

前端协议 (与 web/src/types/index.ts SSEEvent 对齐):
    每个事件一行 JSON, 用 data: 包裹, 事件以双换行分隔.

    {"type": "message_start",   "message_id": "...", "session_id": "..."}
    {"type": "session_created", "session_id": "...", "title": "..."}
    {"type": "session_renamed", "session_id": "...", "title": "..."}
    {"type": "tool_call",       "tool_call": {...}}
    {"type": "tool_result",     "tool_call_id": "...", "result": "...", "status": "success"|"error"}
    {"type": "delta",           "content": "..."}
    {"type": "reasoning_delta", "content": "..."}   // 思考阶段信号 / 思考链增量
                                                     // 1. content="" : 本 step 思考阶段开始, 所有 model 都发,
                                                     //                  让前端统一渲染"思考中".
                                                     // 2. content!="" : 仅支持 thinking 的 model 输出
                                                     //                   (DeepSeek thinking 等), 携带思考链文本.
    {"type": "reasoning_end",    }                  // 本 step 思考阶段结束, 在首个 delta/tool_call 前发出.
    {"type": "citations",       "citations": [...]}
    {"type": "done",            "usage": {...}, "finish_reason": "stop"|"length"|"tool_calls"|"aborted"}
    {"type": "error",           "message": "...", "code": "..."}

实现策略:
    - 本路由层**只做 HTTP / SSE 边界**, 业务逻辑全在 forge.chat.TurnOrchestrator
    - /stop 通过 chat.get_active_streams() 拿到注册表, 跟编排器解耦
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
from forge.api.services.workflow_service import WorkflowServiceDep
from forge.chat import build_turn_orchestrator, get_active_streams
from forge.chat.workflow_dispatcher import (
    resolve_workflow_route,
    stream_workflow_completion,
)
from forge.core.exceptions import NotFound
from forge.core.response import success
from forge.infrastructure.database.database import get_session_factory

# Storage protocol injected, swap by deployment_mode (S6.5 M3).
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
    service: WorkflowServiceDep,
):
    """流式 chat / workflow 统一启动接口 (S6.5 M1).

    路由决策树:
      - body.workflow 为 None             → 走 chat 路径 (TurnOrchestrator), 行为完全等同改造前.
      - body.workflow.template_id 显式    → 走 workflow 路径, 跑指定模板.
      - body.workflow.mode == "auto"      → triage 决定. 命中对话级模板 (question_only) 时
                                            降级回 chat 路径, 否则跑 triage 选中的 workflow 模板.

    SSE 响应:
      - chat 路径事件 (session_created / delta / done / ...) 与原协议一致.
      - workflow 路径事件 (workflow.started / phase.started / phase.completed / ...) 与
        /api/v1/workflows/{id}/events?follow=true 一致.
      - 客户端读首个事件类型即可分辨走向, 不需要预先知道.
    """
    trace_id = getattr(request.state, "trace_id", "")
    client_type = getattr(request.state, "client_type", "cli")
    resolved_template_id = await resolve_workflow_route(
        workflow_option=body.workflow,
        service=service,
        message=body.message,
    )

    if resolved_template_id is None:
        # chat 路径 — 行为零变化.
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
    else:
        # workflow 路径 — 启动后流式透传 workflow 事件.
        pause_after_phase = body.workflow.pause_after_phase if body.workflow else False

        async def event_stream():
            set_client_type(client_type)
            async for event in stream_workflow_completion(
                service=service,
                message=body.message,
                template_id=resolved_template_id,
                pause_after_phase=pause_after_phase,
                user_id=user.id,
                trace_id=trace_id,
            ):
                yield _sse(event)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 反代 nginx 不要缓冲
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
    """继续未完成的 assistant 消息 (status=aborted / partial).

    SSE 输出与 /completions 类似, 区别:
      - 起始事件是 `message_resumed` (而非 message_start), 前端不创建新气泡;
      - 新 delta 追加到旧 content 之后;
      - 终态写库时 content / tool_calls / usage / reasoning 是 prev + 新内容合并.
    """
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
        # 找不到也返回 ok, 不暴露内部状态
        return success(None)
    event.set()
    return success(None)


@router.post("/regenerate")
async def chat_regenerate(body: ChatRegenerateIn, user: CurrentUser):
    """重新生成 assistant 消息.

    实现: 找到 message_id 对应的 assistant 消息, 取其 parent (user 消息),
    在同一 session 内启动新的流. 前端拿到 new_message_id 后再发起 /completions.
    简化: 这里只返回原 user 消息的内容供前端再次提交 /completions.
    """
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

        # 逻辑删除旧的 assistant 消息 (SessionLog append tombstone).
        await repo.delete_by_id(asst.id)

        return success(
            {
                "session_id": session.id,
                "user_message": parent.content,
                # 兼容前端 chatApi.regenerate 的返回类型
                "new_message_id": new_id("msg"),
            }
        )
