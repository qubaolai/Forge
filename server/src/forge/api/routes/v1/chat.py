"""对话接口 (SSE 流式) — 纯聊天服务。

新架构 (长期正解):
    - /completions: 同步 prepare → 启动 ChatTurnRun (背景 task) → SSE 订阅 run
    - /resume:      若该 message 的 TurnRun 还活着, 直接订阅; 否则启动新 resume TurnRun
    - /stop:        supervisor.abort(message_id) — 通知背景 task 结束, 由 finalizer 落库

SSE 协议 (与 web/src/types/index.ts 对齐):
    每个事件一行 JSON, 用 data: 包裹, 事件以双换行分隔.
    新协议额外携带 seq 字段, 客户端可用 ?last_seq=N 断线重连.

    {"seq": 1, "ts": "...", "type": "message_start",   "message_id": "...", ...}
    {"seq": 2, "ts": "...", "type": "session_created", ...}
    {"seq": 3, "ts": "...", "type": "delta",           "content": "..."}
    {"seq": 4, "ts": "...", "type": "tool_call",       "tool_call": {...}}
    {"seq": 5, "ts": "...", "type": "tool_result",     ...}
    {"seq": 6, "ts": "...", "type": "done",            "usage": {...}, "finish_reason": "..."}
    {"seq": 7, "ts": "...", "type": "error",           "message": "...", "code": "..."}
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from forge.api.dependencies import AuthenticatedUser
from forge.api.schemas.chat import (
    ChatCompletionIn,
    ChatRegenerateIn,
    ChatResumeIn,
    ChatStopIn,
)
from forge.chat import build_turn_orchestrator
from forge.chat.preparer import TurnPreparationError
from forge.chat.resumer import ResumeError
from forge.chat.supervisor import get_chat_supervisor
from forge.core.exceptions import NotFound
from forge.core.request_context import set_client_type
from forge.core.response import success
from forge.infrastructure.database.database import get_session_factory
from forge.infrastructure.database.repositories.chat_message_repo import ChatMessageRepository
from forge.infrastructure.database.repositories.chat_session_repo import ChatSessionRepository
from forge.quota import get_usage_quota_manager
from forge.utils.id_generator import new_id

logger = logging.getLogger(__name__)

router = APIRouter()


def _sse(event: dict) -> bytes:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()


async def _single_error_stream(message: str, code: str):
    """prepare/resume 阶段失败 → 单事件 SSE 流."""
    yield _sse({"type": "error", "message": message, "code": code})


def _sse_response(generator) -> StreamingResponse:
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# POST /chat/completions — 新对话
# ---------------------------------------------------------------------------
@router.post("/completions")
async def chat_completions(
    body: ChatCompletionIn,
    user: AuthenticatedUser,
    request: Request,
    last_seq: int = Query(0, ge=0, description="断线重连游标 (新建对话保持 0)"),
):
    """流式聊天对话.

    新架构: 同步建 turn run (背景 task 立即启动), 路由立即返回 SSE 订阅流.
    客户端断开 / 网络异常都不影响背景 task 继续跑直至 finalizer 落库.
    """
    trace_id = getattr(request.state, "trace_id", "")
    client_type = getattr(request.state, "client_type", "cli")
    orchestrator = build_turn_orchestrator()

    try:
        run = await orchestrator.start_turn(
            user_id=user.user_id,
            user_name=user.name,
            body=body,
            trace_id=trace_id,
        )
    except TurnPreparationError as exc:
        return _sse_response(_single_error_stream(exc.message, exc.code))

    async def event_stream():
        set_client_type(client_type)
        try:
            async for record in run.subscribe(last_seq=last_seq):
                yield _sse(record)
        except Exception:  # noqa: BLE001
            logger.exception("SSE 订阅异常 message_id=%s", run.message_id)
            # 订阅链路死了不影响背景 task 继续跑

    return _sse_response(event_stream())


# ---------------------------------------------------------------------------
# POST /chat/resume — 续写 / 接入已有 TurnRun
# ---------------------------------------------------------------------------
@router.post("/resume")
async def chat_resume(
    body: ChatResumeIn,
    user: AuthenticatedUser,
    request: Request,
    last_seq: int = Query(0, ge=0, description="断线重连游标"),
):
    """继续未完成的 assistant 消息.

    路径选择:
        1. 该 message 的 TurnRun 还在内存且未终态 → 直接订阅 (用户重连场景)
        2. TurnRun 已终态或不在内存 → 启动新 resume TurnRun
    """
    trace_id = getattr(request.state, "trace_id", "")
    client_type = getattr(request.state, "client_type", "cli")
    orchestrator = build_turn_orchestrator()
    supervisor = get_chat_supervisor()

    # 1. 优先: 直接接入还在跑的 TurnRun
    existing = supervisor.get(body.message_id)
    if existing is not None and not existing.is_terminal:
        # 鉴权: 不是当前用户的 turn 不能接入
        if existing.user_id != user.user_id:
            return _sse_response(_single_error_stream("无权访问该会话", "40310"))

        async def reattach_stream():
            set_client_type(client_type)
            try:
                async for record in existing.subscribe(last_seq=last_seq):
                    yield _sse(record)
            except Exception:  # noqa: BLE001
                logger.exception("SSE 重连异常 message_id=%s", existing.message_id)

        return _sse_response(reattach_stream())

    # 2. 否则启新 resume turn
    try:
        run = await orchestrator.start_resume(
            user_id=user.user_id,
            message_id=body.message_id,
            trace_id=trace_id,
        )
    except ResumeError as exc:
        return _sse_response(_single_error_stream(exc.message, exc.code))

    async def event_stream():
        set_client_type(client_type)
        try:
            async for record in run.subscribe(last_seq=last_seq):
                yield _sse(record)
        except Exception:  # noqa: BLE001
            logger.exception("SSE 订阅异常 message_id=%s", run.message_id)

    return _sse_response(event_stream())


# ---------------------------------------------------------------------------
# GET /chat/quota
# ---------------------------------------------------------------------------
@router.get("/quota")
async def chat_quota(user: AuthenticatedUser):
    """查看当前用户滚动 LLM 用量额度."""
    status = await get_usage_quota_manager().status(user.user_id)
    return success(status.to_dict())


# ---------------------------------------------------------------------------
# POST /chat/stop
# ---------------------------------------------------------------------------
@router.post("/stop")
async def chat_stop(body: ChatStopIn, user: AuthenticatedUser):
    """中断指定消息的流式生成.

    背景 task 收到 abort 后会自然走 finalizer 落库 (events.jsonl + DB).
    路由本身不再做任何 DB 写入.
    """
    supervisor = get_chat_supervisor()
    run = supervisor.get(body.message_id)

    if run is not None:
        # 鉴权: 不是当前用户的 turn 不能停
        if run.user_id != user.user_id:
            return success(None)
        if not run.is_terminal:
            run.abort()
            logger.info("chat stop: 已通知 turn abort message_id=%s", body.message_id)
        return success(None)

    # 兜底: 进程内无活跃 turn (服务重启 / 已 evict), 但 DB 仍是 streaming
    # → 直接落 status=aborted, 不写 content (本来也没活的可写)
    factory = get_session_factory()
    async with factory() as db:
        repo = ChatMessageRepository(db)
        sess_repo = ChatSessionRepository(db)
        asst = await repo.get_by_id(body.message_id)
        if not asst or asst.role != "assistant":
            return success(None)
        session = await sess_repo.get_by_id(asst.session_id)
        if not session or session.user_id != user.user_id:
            return success(None)
        if asst.status == "streaming":
            await repo.update(asst, status="aborted")
            await db.commit()
            logger.info(
                "chat stop fallback: 遗留 streaming 已置 aborted message_id=%s",
                body.message_id,
            )
    return success(None)


# ---------------------------------------------------------------------------
# POST /chat/regenerate
# ---------------------------------------------------------------------------
@router.post("/regenerate")
async def chat_regenerate(body: ChatRegenerateIn, user: AuthenticatedUser):
    """重新生成 assistant 消息."""
    factory = get_session_factory()
    async with factory() as db:
        repo = ChatMessageRepository(db)
        sess_repo = ChatSessionRepository(db)
        asst = await repo.get_by_id(body.message_id)
        if not asst or asst.role != "assistant":
            raise NotFound("消息不存在", code=40440)

        session = await sess_repo.get_by_id(asst.session_id)
        if not session:
            raise NotFound("会话不存在", code=40410)
        if session.user_id != user.user_id:
            raise NotFound("无权访问该会话", code=40310)

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
