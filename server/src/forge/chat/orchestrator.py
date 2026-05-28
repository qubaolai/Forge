"""TurnOrchestrator: chat 一次 turn 的顶层流程编排。

唯一的"上游入口"。路由层只做:
    orchestrator = build_turn_orchestrator()
    async for event in orchestrator.run_turn(...):
        yield _sse(event.to_dict())

顺序:
    1. TurnPreparer.prepare           -> TurnContext
    2. yield 生命周期事件               -> session_created / session_renamed / message_start
    3. ContextAssembler.assemble       -> AssembledContext + system_prompt
    4. 构造 GatewayLLMAdapter + ReActRunner (统一, 不再有 mode 分支)
    5. TurnFinalizer.finalize          -> 写 DB + 发 done/error + publish turn.completed
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import replace

from forge.config.settings import get_settings

from forge.agents.base import AgentEvent
from forge.core.request_context import set_trace_id, set_user_id
from forge.api.schemas.chat import ChatCompletionIn
from forge.chat.assembler import ContextAssembler
from forge.chat.finalizer import TurnFinalizer
from forge.llm import GatewayBinding, GatewayLLMAdapter, get_llm_gateway
from forge.agents.profiles import get_agent_profile
from forge.chat.preparer import TurnPreparationError, TurnPreparer
from forge.chat.resumer import ResumeError, TurnResumer
from forge.chat.runner import ReActRunner
from forge.chat.types import ResumeState, TurnContext
from forge.core.types.message import Message, ToolCall
from forge.infrastructure.database.database import get_session_factory

from forge.infrastructure.database.repositories.chat_message_repo import ChatMessageRepository
from forge.observability.tracing.tracer import span

logger = logging.getLogger(__name__)

_ACTIVE_STREAMS: dict[str, asyncio.Event] = {}


def get_active_streams() -> dict[str, asyncio.Event]:
    """给 /chat/stop 路由用."""
    return _ACTIVE_STREAMS


class TurnOrchestrator:
    """无状态。每次 run_turn/resume_turn 自有 DB 事务和 LLM 上下文."""

    def __init__(self) -> None:
        self._preparer = TurnPreparer()
        self._assembler = ContextAssembler()
        self._finalizer = TurnFinalizer()
        self._resumer = TurnResumer()

    # ------------------------------------------------------------------
    # run_turn: 全新对话回合
    # ------------------------------------------------------------------
    async def run_turn(
        self,
        *,
        user_id: str,
        user_name: str,
        body: ChatCompletionIn,
        trace_id: str,
    ) -> AsyncIterator[AgentEvent]:
        if trace_id:
            set_trace_id(trace_id)
        if user_id:
            set_user_id(user_id)

        started_at = time.perf_counter()
        assistant_msg_id: str | None = None
        abort_event: asyncio.Event | None = None
        ctx: TurnContext | None = None
        # finalizer 完成后置 True，finally 据此判断是否需要补救落库
        _turn_completed = False
        # 积累已下发的 delta/tool_call，供中断时落库
        _content_parts: list[str] = []
        _tool_calls_buf: list[dict] = []

        logger.info(
            "对话开始 user=%s session_hint=%s input_len=%d",
            user_id, body.session_id or "<new>", len(body.message or ""),
        )

        turn_span_cm = span("chat.turn", user_id=user_id, session_hint=body.session_id or "<new>")
        turn_span_ctx = turn_span_cm.__enter__()
        try:
            # 1. 准备阶段 (DB)
            try:
                ctx = await self._preparer.prepare(
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

            assistant_msg_id = ctx.assistant_msg_id
            abort_event = asyncio.Event()
            _ACTIVE_STREAMS[assistant_msg_id] = abort_event

            # 2. lifecycle events
            for ev in self._lifecycle_events(ctx):
                yield ev

            # 3. context 组装 (支持主动压缩)
            build_result, system_prompt = await self._assembler.assemble(ctx)
            if self._assembler.should_compact(build_result, ctx):
                pre_tokens = build_result.meta.estimated_input_tokens
                trigger_reason = (
                    "history_truncated" if build_result.meta.history_messages_dropped > 0
                    else "approaching_window"
                )
                logger.info(
                    "上下文压缩开始 session=%s reason=%s tokens=%d window=%d history_dropped=%d",
                    ctx.session_id, trigger_reason, pre_tokens,
                    ctx.context_window, build_result.meta.history_messages_dropped,
                )
                yield AgentEvent("compaction_started", {
                    "reason": trigger_reason,
                    "estimated_tokens": pre_tokens,
                    "context_window": ctx.context_window,
                })
                build_result, system_prompt, tokens_saved = (
                    await self._assembler.compact_and_reassemble(ctx, build_result)
                )
                compaction_ok = "compaction_failed" not in build_result.meta.degraded
                logger.info(
                    "上下文压缩完成 session=%s ok=%s tokens %d -> %d (节省 %d) rebuild_count=%d",
                    ctx.session_id, compaction_ok, pre_tokens,
                    build_result.meta.estimated_input_tokens, tokens_saved,
                    build_result.meta.rebuild_count,
                )
                yield AgentEvent("compaction_done", {
                    "tokens_saved": tokens_saved,
                    "estimated_tokens": build_result.meta.estimated_input_tokens,
                    "rebuild_count": build_result.meta.rebuild_count,
                    "ok": compaction_ok,
                })

            # 4. 执行
            runner = await self._setup_runner(
                ctx.agent_mode,
                system_prompt,
                body.model_options.model_dump(),
                user_id=user_id,
            )
            async for ev in runner.run(ctx, build_result.messages, abort_event):
                # 缓冲已下发内容，供中断时落库
                _d = ev.to_dict()
                _t = _d.get("type")
                if _t == "delta":
                    _content_parts.append(_d.get("content") or "")
                elif _t == "tool_call":
                    if _tc := _d.get("tool_call"):
                        _tool_calls_buf.append(dict(_tc))
                elif _t == "tool_result":
                    for _tc in _tool_calls_buf:
                        if _tc.get("id") == _d.get("tool_call_id"):
                            _tc["status"] = _d.get("status")
                            _tc["result"] = _d.get("result")
                            break
                yield ev

            # 5. finalize — 标记完成在前，防止 yield final_event 被打断后 finally 重复清理
            final_event = await self._finalizer.finalize(ctx, runner.result, build_result.meta)
            _turn_completed = True
            if final_event:
                yield final_event

            logger.info(
                "对话完成 session=%s message_id=%s finish_reason=%s content_len=%d "
                "tool_calls=%d duration_ms=%.1f usage=%s",
                ctx.session_id, assistant_msg_id, runner.result.finish_reason,
                len(runner.result.content or ""), len(runner.result.tool_calls or []),
                (time.perf_counter() - started_at) * 1000, runner.result.usage or {},
            )

        except asyncio.CancelledError as exc:
            logger.info("客户端取消对话 (CancelledError): message_id=%s", assistant_msg_id)
            turn_span_ctx.set_error(exc)
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("turn 异常")
            turn_span_ctx.set_error(exc)
            yield AgentEvent("error", {"message": str(exc)})
        finally:
            if ctx is not None:
                turn_span_ctx.set("session_id", ctx.session_id)
            turn_span_ctx.set("duration_ms", (time.perf_counter() - started_at) * 1000)
            turn_span_cm.__exit__(None, None, None)
            if assistant_msg_id:
                _ACTIVE_STREAMS.pop(assistant_msg_id, None)
            # ★ 核心修复：所有未正常完成的 turn（GeneratorExit/CancelledError/Exception）
            # 都在独立 task 中落库。用 create_task 而非 await，因为在 GeneratorExit 上下文
            # 中 await 会被阻断，而 create_task 是同步调度，不受异常类型限制。
            if not _turn_completed and assistant_msg_id:
                try:
                    asyncio.create_task(
                        self._mark_aborted_on_cancel(
                            assistant_msg_id,
                            content="".join(_content_parts),
                            tool_calls=_tool_calls_buf or None,
                        )
                    )
                except RuntimeError:
                    logger.warning("事件循环关闭，无法调度中断清理 message_id=%s", assistant_msg_id)

    # ------------------------------------------------------------------
    # resume_turn: 继续 aborted/partial 的 assistant 消息
    # ------------------------------------------------------------------
    async def resume_turn(
        self,
        *,
        user_id: str,
        message_id: str,
        trace_id: str,
    ) -> AsyncIterator[AgentEvent]:
        if trace_id:
            set_trace_id(trace_id)
        if user_id:
            set_user_id(user_id)

        started_at = time.perf_counter()
        assistant_msg_id: str | None = None
        abort_event: asyncio.Event | None = None
        ctx: TurnContext | None = None
        prev_state: ResumeState | None = None
        _turn_completed = False
        # 积累本次续写新增的 delta/tool_call
        _content_parts: list[str] = []
        _tool_calls_buf: list[dict] = []

        logger.info("续写开始 user=%s message_id=%s", user_id, message_id)

        try:
            try:
                ctx, prev_state = await self._resumer.prepare(
                    user_id=user_id, message_id=message_id, trace_id=trace_id,
                )
            except ResumeError as exc:
                yield AgentEvent("error", {"message": exc.message, "code": exc.code})
                return

            assistant_msg_id = ctx.assistant_msg_id
            abort_event = asyncio.Event()
            _ACTIVE_STREAMS[assistant_msg_id] = abort_event

            yield AgentEvent("message_resumed", {
                "message_id": assistant_msg_id,
                "session_id": ctx.session_id,
                "prev_content_len": len(prev_state.prev_content),
                "prev_reason": prev_state.prev_finish_reason,
            })

            # 增强 resume prompt: 注入中断前输出的尾部
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

            build_result, system_prompt = await self._assembler.assemble(ctx)
            messages = _inject_partial_into_messages(build_result.messages, prev_state)

            runner = await self._setup_runner(
                ctx.agent_mode,
                system_prompt,
                None,
                user_id=user_id,
            )
            async for ev in runner.run(ctx, messages, abort_event):
                # 缓冲本次续写新增内容
                _d = ev.to_dict()
                _t = _d.get("type")
                if _t == "delta":
                    _content_parts.append(_d.get("content") or "")
                elif _t == "tool_call":
                    if _tc := _d.get("tool_call"):
                        _tool_calls_buf.append(dict(_tc))
                elif _t == "tool_result":
                    for _tc in _tool_calls_buf:
                        if _tc.get("id") == _d.get("tool_call_id"):
                            _tc["status"] = _d.get("status")
                            _tc["result"] = _d.get("result")
                            break
                yield ev

            final_event = await self._finalizer.finalize(
                ctx, runner.result, build_result.meta, prev_state=prev_state,
            )
            _turn_completed = True
            if final_event:
                yield final_event

            logger.info(
                "续写完成 session=%s message_id=%s finish_reason=%s delta_content_len=%d "
                "duration_ms=%.1f",
                ctx.session_id, assistant_msg_id, runner.result.finish_reason,
                len(runner.result.content or ""),
                (time.perf_counter() - started_at) * 1000,
            )

        except asyncio.CancelledError:
            logger.info("客户端取消续写 (CancelledError): message_id=%s", assistant_msg_id)
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("resume 异常")
            yield AgentEvent("error", {"message": str(exc)})
        finally:
            if assistant_msg_id:
                _ACTIVE_STREAMS.pop(assistant_msg_id, None)
            # ★ 核心修复：合并上次内容 + 本次新增，在独立 task 中落库
            if not _turn_completed and assistant_msg_id:
                _prev_content = prev_state.prev_content if prev_state else ""
                _new_content = "".join(_content_parts)
                _prev_tcs = list(prev_state.prev_tool_calls or []) if prev_state else []
                try:
                    asyncio.create_task(
                        self._mark_aborted_on_cancel(
                            assistant_msg_id,
                            content=_prev_content + _new_content,
                            tool_calls=(_prev_tcs + _tool_calls_buf) or None,
                        )
                    )
                except RuntimeError:
                    logger.warning("事件循环关闭，无法调度续写中断清理 message_id=%s", assistant_msg_id)

    # ------------------------------------------------------------------
    # _setup_runner: 构造 GatewayLLMAdapter + ReActRunner.from_profile
    # ------------------------------------------------------------------
    async def _setup_runner(
        self,
        agent_mode: str,
        system_prompt: str,
        model_options,
        *,
        user_id: str | None = None,
    ) -> ReActRunner:
        settings = get_settings()
        provider = model_options.get("provider") if model_options else None
        model = model_options.get("model") if model_options else None

        # Chat 路径: 用户在前端选了 provider/model 直接 pin; 没选则交给 Router 决策.
        binding = GatewayBinding(
            gateway=get_llm_gateway(settings),
            user_id=user_id,
            preferred_provider=provider,
            preferred_model=model,
            task_type="chat",
        )
        llm = GatewayLLMAdapter(binding)

        # mode 路由 = profile 查询. 启动期已校验; 运行期未知 mode 抛 ValueError.
        profile = get_agent_profile(agent_mode)
        return ReActRunner.from_profile(
            llm,
            profile,
            system_prompt=system_prompt,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _lifecycle_events(ctx: TurnContext):
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
    async def _mark_aborted_on_cancel(
        assistant_msg_id: str,
        *,
        content: str = "",
        tool_calls: list | None = None,
    ) -> None:
        try:
            factory = get_session_factory()
            async with factory() as db:
                repo = ChatMessageRepository(db)
                msg = await repo.get_by_id(assistant_msg_id)
                if msg and msg.status == "streaming":
                    await repo.update(
                        msg,
                        status="aborted",
                        content=content or None,
                        tool_calls=tool_calls or None,
                    )
                await db.commit()
            logger.info(
                "cancel 清理完成 message_id=%s content_len=%d tool_calls=%d",
                assistant_msg_id, len(content), len(tool_calls or []),
            )
        except Exception:  # noqa: BLE001
            logger.exception("cancel 清理失败 message_id=%s", assistant_msg_id)


# ---------------------------------------------------------------------------
# Resume 辅助
# ---------------------------------------------------------------------------
def _inject_partial_into_messages(
    base_messages: list[Message], prev_state: ResumeState
) -> list[Message]:
    """把上次的 partial assistant + 完成态 tool 结果插入 messages 末尾."""
    if not base_messages:
        return base_messages
    if not prev_state.prev_content and not prev_state.prev_tool_calls:
        return base_messages

    completed_calls: list[dict] = []
    tool_msgs: list[Message] = []
    for tc in prev_state.prev_tool_calls:
        status = tc.get("status")
        if status not in ("success", "error"):
            continue
        completed_calls.append(tc)
        tool_msgs.append(Message(
            role="tool",
            content=tc.get("result") or "",
            tool_call_id=tc.get("id"),
            name=tc.get("tool_name") or tc.get("tool_id"),
        ))

    partial_asst = Message(
        role="assistant",
        content=prev_state.prev_content or "",
        tool_calls=_dicts_to_tool_calls(completed_calls),
        extra_content=prev_state.prev_reasoning_content,
    )

    return base_messages[:-1] + [partial_asst, *tool_msgs] + [base_messages[-1]]


def _dicts_to_tool_calls(records: list[dict]) -> list[ToolCall]:
    out: list[ToolCall] = []
    for r in records:
        out.append(ToolCall(
            id=r.get("id") or "",
            name=r.get("tool_name") or r.get("tool_id") or "",
            arguments=r.get("arguments") or {},
        ))
    return out


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------
def build_turn_orchestrator() -> TurnOrchestrator:
    return TurnOrchestrator()
