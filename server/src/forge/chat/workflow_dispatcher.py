"""Chat 入口到 workflow 的分流桥 (S6.5 M1).

职责:
1. 判断 `ChatCompletionIn.workflow` 字段是否应进入 workflow 路径.
2. 若进入: 启动 workflow + 订阅 EventBus + 把事件以 dict 形式 yield 给路由层,
   路由层用 chat 路由的 `_sse` 包装,与 chat 路径共享同一个 StreamingResponse.
3. `mode="auto"` 下命中"对话级模板"(当前仅 question_only)时返回 None,
   让路由层降级回原 chat 路径, 不再套 workflow 外壳.

不做:
- 不在内部做 HTTP 自调用 (直接调 WorkflowService).
- 不重新设计 SSE envelope (workflow 事件原样透传).
- 不阻塞调用方 (workflow start 是 fire-and-forget, 立即开始流事件).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator

from forge.api.schemas.chat import ChatWorkflowOption
from forge.api.services.workflow_service import WorkflowService
from forge.infrastructure.event_bus import get_event_bus

logger = logging.getLogger(__name__)

# `mode="auto"` 下,这些模板被视为"对话级",路由层降级回 chat 路径,
# 不通过 workflow 引擎,避免单轮问答吃 phase 状态机 overhead.
CHAT_FALLBACK_TEMPLATES: frozenset[str] = frozenset({"question_only"})

# SSE follow 的总超时上限(秒). workflow 完成 / 失败 / 中止 即返回; 超时也返回.
WORKFLOW_FOLLOW_TIMEOUT_SEC: float = 600.0


async def resolve_workflow_route(
    *,
    workflow_option: ChatWorkflowOption | None,
    service: WorkflowService,
    message: str,
) -> str | None:
    """返回应跑的 workflow template_id; 返回 None 表示走 chat 路径.

    决策表:
    - workflow_option 为 None → None (走 chat).
    - workflow_option.template_id 显式 → 直接用该模板.
    - workflow_option.mode == "auto" → triage 决定; 命中 CHAT_FALLBACK_TEMPLATES 时降级回 chat.
    """
    if workflow_option is None:
        return None
    if workflow_option.template_id:
        return workflow_option.template_id
    if workflow_option.mode == "auto":
        decision = await service.orchestrator.triage(message)
        logger.info(
            "auto 路由决策 template=%s intent=%s reason=%s",
            decision.template_id,
            decision.intent,
            decision.reason,
        )
        if decision.template_id in CHAT_FALLBACK_TEMPLATES:
            return None
        return decision.template_id
    return None


async def stream_workflow_completion(
    *,
    service: WorkflowService,
    message: str,
    template_id: str,
    pause_after_phase: bool,
    user_id: str,
    trace_id: str,
) -> AsyncIterator[dict]:
    """启动 workflow 并以 dict 形式 yield 事件, 直到 workflow 终态或超时.

    事件协议沿用既有 workflow 事件 (workflow.started / phase.* / workflow.completed
    等), 不引入新事件类型. 调用方需要自己 SSE 包装.
    """
    state = await service.start(
        message=message,
        template_id=template_id,
        workspace_path=None,
        pause_after_phase=pause_after_phase,
        metadata=None,
        owner_user_id=user_id,
    )
    workflow_id = state["workflow_id"]
    logger.info(
        "对话入口分流到 workflow workflow_id=%s template_id=%s trace_id=%s",
        workflow_id,
        template_id,
        trace_id,
    )

    wakeup = asyncio.Event()
    bus = get_event_bus()

    async def _on_workflow_event(payload: dict) -> None:
        if payload.get("workflow_id") == workflow_id:
            wakeup.set()

    unsubscribe = bus.subscribe("workflow.event", _on_workflow_event)
    last_id: str | None = None
    deadline = time.monotonic() + WORKFLOW_FOLLOW_TIMEOUT_SEC

    try:
        # 1) 回放当前已落盘事件 (workflow.started / triaged 等已在 start() 内 emit).
        events = await service.events(
            workflow_id,
            from_event_id=last_id,
            requester_user_id=user_id,
        )
        for event in events:
            last_id = event["id"]
            yield event

        current = await service.get(workflow_id, requester_user_id=user_id)

        # 2) 持续 follow, EventBus 唤醒. 终态 / 超时即结束.
        while current.get("status") not in {"completed", "aborted", "failed"}:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning(
                    "workflow follow 超时 workflow_id=%s status=%s",
                    workflow_id,
                    current.get("status"),
                )
                break
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(wakeup.wait(), timeout=min(remaining, 30.0))
            wakeup.clear()

            events = await service.events(
                workflow_id,
                from_event_id=last_id,
                requester_user_id=user_id,
            )
            for event in events:
                last_id = event["id"]
                yield event
            current = await service.get(workflow_id, requester_user_id=user_id)

        # 3) 终态后再 drain 一次, 捕获 fetch ↔ status 检查之间到达的最后事件
        # (典型: workflow.completed 在 phase.completed 之后立刻 emit, 而上一次
        # events 拉取可能错过它).
        events = await service.events(
            workflow_id,
            from_event_id=last_id,
            requester_user_id=user_id,
        )
        for event in events:
            last_id = event["id"]
            yield event
    finally:
        try:
            unsubscribe()
        except Exception:  # noqa: BLE001
            logger.exception("workflow follow 取消订阅失败 workflow_id=%s", workflow_id)
