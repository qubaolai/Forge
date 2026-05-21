"""TurnOrchestrator: chat 一次 turn 的顶层流程编排.

唯一的"上游入口". 路由层只做:
    orchestrator = build_turn_orchestrator()
    async for event in orchestrator.run_turn(...):
        yield _sse(event.to_dict())

顺序:
    1. TurnPreparer.prepare           -> TurnContext + AgentSnapshot
    2. yield 生命周期事件               -> session_created / session_renamed / message_start
    3. ContextAssembler.assemble       -> AssembledContext + system_prompt
    4. resolve LLM chain               -> chat.llm_selection
    5. 按 agent.mode 选 Runner 类       -> chat.runner._RUNNER_REGISTRY
    6. 跑 runner.run, 透传中间事件      -> delta / tool_call / tool_result / reasoning_delta / reasoning_end
    7. TurnFinalizer.finalize          -> 写 DB + 发 done/error + publish turn.completed

异常:
    - TurnPreparationError -> 发 SSE error 事件直接 return
    - CancelledError       -> 走 cancel 清理路径, 重抛
    - 其他                 -> SSE error + 兜底

abort_event:
    - 主动注册到 _ACTIVE_STREAMS, 给 /chat/stop 用
    - finally 时 pop
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from typing import Any, cast

from config.settings import get_settings

from forge.agents.base import AgentEvent
from forge.api.middleware.tracing import _TRACE_ID, _USER_ID
from forge.api.schemas.chat import ChatCompletionIn
from forge.chat.assembler import ContextAssembler
from forge.chat.finalizer import TurnFinalizer
from forge.chat.llm_selection import build_llm_chain_for_agent
from forge.chat.preparer import TurnPreparationError, TurnPreparer
from forge.chat.resumer import ResumeError, TurnResumer
from forge.chat.runner import get_runner_class, supported_modes
from forge.chat.types import ResumeState, TurnContext
from forge.core.types.message import Message, ToolCall
from forge.infrastructure.database.database import get_session_factory

# Storage protocol injected, swap by deployment_mode (S6.5 M3).
from forge.infrastructure.storage import make_message_store
from forge.observability.tracing.tracer import span
from forge.tools.base import Tool

logger = logging.getLogger(__name__)

# /chat/stop 用的全局注册表: message_id -> asyncio.Event
# 单一进程内, 跟原 _stream_chat 行为一致.
_ACTIVE_STREAMS: dict[str, asyncio.Event] = {}


def get_active_streams() -> dict[str, asyncio.Event]:
    """给 /chat/stop 路由用."""
    return _ACTIVE_STREAMS


@dataclass(frozen=True)
class RunTurnOverrides:
    """workflow 调 chat 主链路时的内部覆盖参数."""

    role: str = "local"
    tools: tuple[Tool, ...] | None = None
    max_steps: int | None = None
    model_id: str | None = None
    system_prompt: str | None = None
    model_options: dict[str, Any] | None = None
    workspace_id: str | None = None
    workflow_id: str | None = None
    workspace_context: Any | None = None
    workflow_context: Any | None = None
    project_decisions: tuple[str, ...] = ()
    role_history: tuple[str, ...] = ()


class TurnOrchestrator:
    def __init__(
        self,
        *,
        preparer: TurnPreparer | None = None,
        assembler: ContextAssembler | None = None,
        finalizer: TurnFinalizer | None = None,
        resumer: TurnResumer | None = None,
    ) -> None:
        self._preparer = preparer or TurnPreparer()
        self._assembler = assembler or ContextAssembler()
        self._finalizer = finalizer or TurnFinalizer()
        self._resumer = resumer or TurnResumer()

    async def run_turn(
        self,
        *,
        user_id: str,
        user_name: str,
        body: ChatCompletionIn,
        trace_id: str,
        overrides: RunTurnOverrides | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """跑一整个 turn, yield AgentEvent (调用方负责 SSE 包装)."""
        # starlette BaseHTTPMiddleware 在 StreamingResponse body 阶段会丢 contextvar,
        # 这里显式重设, 保证生成器内部的日志能带上 trace_id.
        if trace_id:
            _TRACE_ID.set(trace_id)
        # user_id 走独立 ContextVar (cost / audit / metrics 用), 由 orchestrator 显式 set:
        # middleware 早于 auth 拿不到 user, route 层的 CurrentUser 才有, 透传到这里.
        if user_id:
            _USER_ID.set(user_id)

        started_at = time.perf_counter()
        assistant_msg_id: str | None = None
        abort_event: asyncio.Event | None = None
        ctx: TurnContext | None = None

        logger.info(
            "对话开始 user=%s session_hint=%s agent_hint=%s input_len=%d",
            user_id,
            body.session_id or "<new>",
            body.agent_id or "default",
            len(body.message or ""),
        )

        turn_span_cm = span(
            "chat.turn",
            user_id=user_id,
            session_hint=body.session_id or "<new>",
            workspace_id=(overrides.workspace_id if overrides is not None else None),
            workflow_id=(overrides.workflow_id if overrides is not None else None),
        )
        turn_span_ctx = turn_span_cm.__enter__()
        try:
            # 1. 准备阶段 (DB)
            try:
                ctx, agent_snapshot = await self._preparer.prepare(
                    user_id=user_id,
                    user_name=user_name,
                    session_id=body.session_id,
                    message=body.message,
                    agent_id_hint=body.agent_id,
                    trace_id=trace_id,
                    model_options=body.model_options,
                )
            except TurnPreparationError as exc:
                yield AgentEvent("error", {"message": exc.message, "code": exc.code})
                return

            if overrides is not None:
                agent_snapshot = _apply_snapshot_overrides(agent_snapshot, overrides)
                ctx = replace(
                    ctx,
                    workspace_id=overrides.workspace_id,
                    workflow_id=overrides.workflow_id,
                    workspace_context=overrides.workspace_context,
                    workflow_context=overrides.workflow_context,
                    project_decisions=overrides.project_decisions,
                    role_history=overrides.role_history,
                )
                if overrides.model_options:
                    merged_options = dict(ctx.model_options or {})
                    merged_options.update(overrides.model_options)
                    ctx = replace(ctx, model_options=merged_options)

            assistant_msg_id = ctx.assistant_msg_id

            # 2. 注册 abort_event (在发 message_start 之前完成, /chat/stop 能立刻挂上)
            abort_event = asyncio.Event()
            _ACTIVE_STREAMS[assistant_msg_id] = abort_event

            # 3. lifecycle events
            for ev in self._lifecycle_events(ctx):
                yield ev

            # 4. context 组装 (支持主动压缩)
            build_result, system_prompt = await self._assembler.assemble(ctx, agent_snapshot)
            if self._assembler.should_compact(build_result, ctx):
                # 上下文吃紧 -> 内部跑 SummaryService + 重建. 用户感知:
                # 前端看到 compaction_started -> 几秒后 compaction_done.
                pre_tokens = build_result.meta.estimated_input_tokens
                trigger_reason = (
                    "history_truncated"
                    if build_result.meta.history_messages_dropped > 0
                    else "approaching_window"
                )
                logger.info(
                    "上下文压缩开始 session=%s reason=%s tokens=%d window=%d history_dropped=%d",
                    ctx.session_id,
                    trigger_reason,
                    pre_tokens,
                    ctx.context_window,
                    build_result.meta.history_messages_dropped,
                )
                yield AgentEvent(
                    "compaction_started",
                    {
                        "reason": trigger_reason,
                        "estimated_tokens": pre_tokens,
                        "context_window": ctx.context_window,
                    },
                )
                build_result, system_prompt, tokens_saved = (
                    # 压缩重新构造上下文
                    await self._assembler.compact_and_reassemble(ctx, agent_snapshot, build_result)
                )
                compaction_ok = "compaction_failed" not in build_result.meta.degraded
                logger.info(
                    "上下文压缩完成 session=%s ok=%s tokens %d -> %d (节省 %d) rebuild_count=%d",
                    ctx.session_id,
                    compaction_ok,
                    pre_tokens,
                    build_result.meta.estimated_input_tokens,
                    tokens_saved,
                    build_result.meta.rebuild_count,
                )
                yield AgentEvent(
                    "compaction_done",
                    {
                        "tokens_saved": tokens_saved,
                        "estimated_tokens": build_result.meta.estimated_input_tokens,
                        "rebuild_count": build_result.meta.rebuild_count,
                        # compaction_failed 走的是 prev 结果, degraded 里会有标记
                        "ok": compaction_ok,
                    },
                )

            # 5. LLM chain (per turn 构, 但 LLM 客户端走 LLMClientPool 池化)
            settings = get_settings()
            llm_chain = build_llm_chain_for_agent(_agent_snapshot_for_llm(agent_snapshot), settings)

            # 显式日志 model_options request级覆盖
            if body.model_options:
                logger.info("model_options 单次覆盖: %s", body.model_options)

            # 6. Runner 分发
            runner_cls = get_runner_class(ctx.agent_mode)
            if runner_cls is None:
                yield AgentEvent(
                    "error",
                    {
                        "message": (
                            f"暂不支持 agent.mode={ctx.agent_mode!r}, "
                            f"当前已注册 {supported_modes()}"
                        ),
                        "code": "50301",
                    },
                )
                return

            # max_steps 从硬限制变成 循环+软防护(提示词防护)
            # (StepSafetyNet) 在最后几步软引导收尾, 用户体验上几乎到不了上限.
            runner = _instantiate_runner(
                runner_cls,
                llm_chain,
                system_prompt,
                role=overrides.role if overrides is not None else "local",
                tools=overrides.tools if overrides is not None else None,
                max_steps=overrides.max_steps if overrides is not None else None,
            )

            # 7. 跑 runner, 透传中间事件
            async for ev in runner.run(ctx, build_result.messages, abort_event):
                yield ev

            # 8. finalize: 写 DB + 发终态事件 + publish turn.completed
            async for ev in self._finalizer.finalize(ctx, runner.result, build_result.meta):
                yield ev

            logger.info(
                "对话完成 session=%s message_id=%s finish_reason=%s "
                "content_len=%d tool_calls=%d duration_ms=%.1f usage=%s",
                ctx.session_id,
                assistant_msg_id,
                runner.result.finish_reason,
                len(runner.result.content or ""),
                len(runner.result.tool_calls or []),
                (time.perf_counter() - started_at) * 1000,
                runner.result.usage or {},
            )

        except asyncio.CancelledError as exc:
            # 客户端断开 (浏览器关闭 / 网络中断)
            logger.info("客户端中断对话: message_id=%s", assistant_msg_id)
            turn_span_ctx.set_error(exc)
            if assistant_msg_id:
                await self._mark_aborted_on_cancel(assistant_msg_id)
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("turn 异常")
            turn_span_ctx.set_error(exc)
            yield AgentEvent("error", {"message": str(exc)})
        finally:
            if ctx is not None:
                if ctx.workspace_id:
                    turn_span_ctx.set("workspace_id", ctx.workspace_id)
                if ctx.workflow_id:
                    turn_span_ctx.set("workflow_id", ctx.workflow_id)
                turn_span_ctx.set("session_id", ctx.session_id)
            turn_span_ctx.set("duration_ms", (time.perf_counter() - started_at) * 1000)
            turn_span_cm.__exit__(None, None, None)
            if assistant_msg_id:
                _ACTIVE_STREAMS.pop(assistant_msg_id, None)

    # ------------------------------------------------------------------
    # Resume: 把 aborted/partial 的 assistant 消息接着跑
    # ------------------------------------------------------------------
    async def resume_turn(
        self,
        *,
        user_id: str,
        message_id: str,
        trace_id: str,
    ) -> AsyncIterator[AgentEvent]:
        """继续生成已暂停的 assistant 消息.

        SSE 协议:
            1. message_resumed   {message_id, session_id, prev_content_len, prev_reason}
                                 - 前端: 这条 message 的旧内容还在, 接着追加 delta 即可
            2. delta / tool_call / tool_result / reasoning_delta / reasoning_end   (Runner 透传)
            3. 终态:
                 done                                              - 全部完成
                 task_partial                                      - 又被中断, 还能再 resume
                 error                                             - 不可恢复
        """
        if trace_id:
            _TRACE_ID.set(trace_id)
        if user_id:
            _USER_ID.set(user_id)

        started_at = time.perf_counter()
        assistant_msg_id: str | None = None
        abort_event: asyncio.Event | None = None
        ctx: TurnContext | None = None

        logger.info("续写开始 user=%s message_id=%s", user_id, message_id)

        try:
            # 1. resume preparation: 加载 + 校验 + status 回滚到 streaming
            try:
                ctx, agent_snapshot, prev_state = await self._resumer.prepare(
                    user_id=user_id,
                    message_id=message_id,
                    trace_id=trace_id,
                )
            except ResumeError as exc:
                yield AgentEvent("error", {"message": exc.message, "code": exc.code})
                return

            assistant_msg_id = ctx.assistant_msg_id

            # 2. abort_event 注册 (复用同 message_id, /chat/stop 能就地挂上)
            abort_event = asyncio.Event()
            _ACTIVE_STREAMS[assistant_msg_id] = abort_event

            # 3. message_resumed 事件 (跟 message_start 区分; 前端不创建新气泡)
            yield AgentEvent(
                "message_resumed",
                {
                    "message_id": assistant_msg_id,
                    "session_id": ctx.session_id,
                    "prev_content_len": len(prev_state.prev_content),
                    "prev_reason": prev_state.prev_finish_reason,
                },
            )

            # 4. 增强 resume prompt: 把中断前输出的尾部注入, 让 LLM 明确知道从哪里继续
            prev_tail = (
                prev_state.prev_content[-500:]
                if len(prev_state.prev_content) > 500
                else prev_state.prev_content
            )
            if prev_tail:
                ctx = replace(
                    ctx,
                    current_user_message=(
                        f"{ctx.current_user_message}\n\n"
                        f"(以下是你中断前输出的最后部分, 请从此之后继续, 不要重复)\n"
                        f">>>\n{prev_tail}\n<<<"
                    ),
                )

            # 5. 走 Assembler 拼基础上下文 (含 system / history / <current_question>resume prompt</...>)
            build_result, system_prompt = await self._assembler.assemble(ctx, agent_snapshot)

            # 6. 插入上次的 partial assistant 消息 (含 tool_calls) + 完成态 tool 结果,
            #    让 LLM 看到 "自己之前说过什么 + 工具调用结果", 然后接着 <current_question> 继续.
            messages = _inject_partial_into_messages(
                base_messages=build_result.messages,
                prev_state=prev_state,
            )

            # 7. LLM chain
            settings = get_settings()
            llm_chain = build_llm_chain_for_agent(_agent_snapshot_for_llm(agent_snapshot), settings)

            # 8. Runner
            runner_cls = get_runner_class(ctx.agent_mode)
            if runner_cls is None:
                yield AgentEvent(
                    "error",
                    {
                        "message": f"暂不支持 agent.mode={ctx.agent_mode!r}",
                        "code": "50301",
                    },
                )
                return

            runner = _instantiate_runner(
                runner_cls,
                llm_chain,
                system_prompt,
            )
            async for ev in runner.run(ctx, messages, abort_event):
                yield ev

            # 9. Finalizer (传 prev_state 做合并)
            async for ev in self._finalizer.finalize(
                ctx, runner.result, build_result.meta, prev_state=prev_state
            ):
                yield ev

            logger.info(
                "续写完成 session=%s message_id=%s finish_reason=%s "
                "delta_content_len=%d duration_ms=%.1f",
                ctx.session_id,
                assistant_msg_id,
                runner.result.finish_reason,
                len(runner.result.content or ""),
                (time.perf_counter() - started_at) * 1000,
            )

        except asyncio.CancelledError:
            logger.info("客户端中断续写: message_id=%s", assistant_msg_id)
            if assistant_msg_id:
                await self._mark_aborted_on_cancel(assistant_msg_id)
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("resume 异常")
            yield AgentEvent("error", {"message": str(exc)})
        finally:
            if assistant_msg_id:
                _ACTIVE_STREAMS.pop(assistant_msg_id, None)

    # ------------------------------------------------------------------
    @staticmethod
    def _lifecycle_events(ctx: TurnContext):
        """session_created / session_renamed / message_start 三类生命周期事件."""
        if ctx.is_new_session:
            yield AgentEvent(
                "session_created",
                {"session_id": ctx.session_id, "title": ctx.new_title or "新会话"},
            )
        elif ctx.new_title:
            yield AgentEvent(
                "session_renamed",
                {"session_id": ctx.session_id, "title": ctx.new_title},
            )
        yield AgentEvent(
            "message_start",
            {"message_id": ctx.assistant_msg_id, "session_id": ctx.session_id},
        )

    @staticmethod
    async def _mark_aborted_on_cancel(assistant_msg_id: str) -> None:
        try:
            factory = get_session_factory()
            async with factory() as db:
                repo = make_message_store(db)
                msg = await repo.get_by_id(assistant_msg_id)
                if msg and msg.status == "streaming":
                    await repo.update(msg, status="aborted")
                await db.commit()
        except Exception:  # noqa: BLE001
            logger.exception("cancel 清理失败 message_id=%s", assistant_msg_id)


def _agent_snapshot_for_llm(snapshot):
    """LLMSelection 期望一个有 .model_id 字段的对象 (AgentOrm 或 _AgentSnapshot 都行).

    _AgentSnapshot 已经有 .model_id, 直接 pass-through.
    """
    return snapshot


def _apply_snapshot_overrides(snapshot, overrides: RunTurnOverrides):
    updates: dict[str, Any] = {}
    if overrides.model_id:
        updates["model_id"] = overrides.model_id
    if overrides.system_prompt is not None:
        updates["system_prompt"] = overrides.system_prompt
    if not updates:
        return snapshot
    return replace(snapshot, **updates)


def _instantiate_runner(
    runner_cls,
    llm_chain,
    system_prompt: str,
    *,
    role: str = "local",
    tools: tuple[Tool, ...] | None = None,
    max_steps: int | None = None,
):
    """实例化 runner.

    Runner 注册表当前只有 ReActRunner; 用 Any cast 保持注册表未来可扩展,
    同时不让 Protocol 的构造器签名影响调用侧。
    """
    cls = cast(Any, runner_cls)
    kwargs: dict[str, Any] = {
        "system_prompt": system_prompt,
        "role": role,
        "tools": tools,
    }
    if max_steps is not None:
        kwargs["max_steps"] = max_steps
    return cls(llm_chain, **kwargs)


# ---------------------------------------------------------------------------
# Resume 辅助: 把 partial assistant + 完成态 tool 消息插进 messages
# ---------------------------------------------------------------------------
def _inject_partial_into_messages(
    base_messages: list[Message], prev_state: ResumeState
) -> list[Message]:
    """把上次的 assistant + tool 结果塞到 messages 末尾 (resume prompt 之前).

    base_messages 结构 (来自 Assembler):
        [system, ...history..., wrapped_resume_prompt]

    返回:
        [system, ...history..., partial_assistant, *tool_results, wrapped_resume_prompt]

    完成态 tool_calls (status=success/error) 携带 result, 转成 tool 消息;
    未完成的 (status=running 或 None) 整体丢弃 (连 partial_assistant.tool_calls
    里都剔掉), 避免 OpenAI 报 "tool_call 没有对应 tool_result" 错.
    """
    if not base_messages:
        return base_messages
    if not prev_state.prev_content and not prev_state.prev_tool_calls:
        # 上次没文字也没工具, 没什么可注入的
        return base_messages

    # 1. 按 status 拆 tool_calls
    completed_calls: list[dict] = []
    tool_msgs: list[Message] = []
    for tc in prev_state.prev_tool_calls:
        status = tc.get("status")
        if status not in ("success", "error"):
            continue
        completed_calls.append(tc)
        tool_msgs.append(
            Message(
                role="tool",
                content=tc.get("result") or "",
                tool_call_id=tc.get("id"),
                name=tc.get("tool_name") or tc.get("tool_id"),
            )
        )

    # 2. 重建 partial assistant 消息 (只带完成态 tool_calls)
    partial_asst = Message(
        role="assistant",
        content=prev_state.prev_content or "",
        tool_calls=_dicts_to_tool_calls(completed_calls),
        reasoning_content=prev_state.prev_reasoning_content,
    )

    # 3. 插到 wrapped_resume_prompt 之前
    return base_messages[:-1] + [partial_asst, *tool_msgs] + [base_messages[-1]]


def _dicts_to_tool_calls(records: list[dict]) -> list[ToolCall]:
    """把 DB JSON 里的 tool_call 记录 -> ToolCall 对象 (给 Message.tool_calls 用)."""
    out: list[ToolCall] = []
    for r in records:
        out.append(
            ToolCall(
                id=r.get("id") or "",
                name=r.get("tool_name") or r.get("tool_id") or "",
                arguments=r.get("arguments") or {},
            )
        )
    return out


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------
def build_turn_orchestrator() -> TurnOrchestrator:
    """单例工厂. Orchestrator 本身无状态, 复用即可."""
    return TurnOrchestrator()
