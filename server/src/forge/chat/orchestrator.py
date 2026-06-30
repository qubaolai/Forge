"""TurnOrchestrator: chat 一次 turn 的顶层编排.

新架构 (长期正解):
    - 旧版是一个巨型 async generator, SSE 链路和 agent 跑完全绑死
    - 新版只负责 "建好 TurnRun + attach 背景执行协程"; SSE 由路由层订阅 run
    - agent 跑在独立 asyncio.Task, 客户端断开不影响落库

对外入口:
    orchestrator = build_turn_orchestrator()
    run = await orchestrator.start_turn(...)         # 新对话
    run = await orchestrator.start_resume(...)       # 续写
    async for event in run.subscribe(last_seq=N):    # SSE 订阅
        yield _sse(event)

落库语义:
    - 每个 agent event 经 run.emit() → fsync 写 events.jsonl + broadcast
    - finalizer 在 agent stream 结束后跑 (背景 task 里), 把最终 content 写 DB
    - 客户端断 / 路由死都不会阻断这条路径
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import replace
from datetime import datetime

from forge.agents.base import AgentEvent
from forge.agents.profiles import get_agent_profile
from forge.api.schemas.chat import ChatCompletionIn
from forge.chat.finalizer import TurnFinalizer
from forge.chat.kb_resolver import fetch_kb_list
from forge.chat.preparer import TurnPreparer
from forge.chat.resumer import ResumeError, TurnResumer
from forge.chat.runner import ReActRunner
from forge.chat.supervisor import get_chat_supervisor
from forge.chat.turn_run import (
    TERMINAL_ABORTED,
    TERMINAL_ERROR,
    TERMINAL_OK,
    TERMINAL_PARTIAL,
    ChatTurnRun,
)
from forge.chat.types import ResumeState, TurnContext
from forge.config.settings import get_settings
from forge.context_mgmt.builder.factory import build_context_builder
from forge.context_mgmt.compaction.controller import CompactionController
from forge.context_mgmt.compaction.strategies.summary import SummaryCompaction
from forge.context_mgmt.compaction.trigger.threshold import ThresholdTrigger
from forge.context_mgmt.manager import ContextManager
from forge.context_mgmt.memory_factory import get_memory_store
from forge.context_mgmt.types import (
    CompactionResult,
    ContextRequest,
    ContextSnapshot,
)
from forge.core.content_merge import ResumeStreamDedup
from forge.core.request_context import (
    collected_citations,
    set_assistant_message_id,
    set_session_id,
    set_trace_id,
    set_user_id,
    start_citation_collection,
)
from forge.core.types.message import Message, ToolCall
from forge.infrastructure.database.database import session_scope
from forge.infrastructure.database.repositories.chat_message_repo import ChatMessageRepository
from forge.llm import GatewayBinding, GatewayLLMAdapter, get_llm_gateway
from forge.prompts import get_registry

logger = logging.getLogger(__name__)

_CHAT_SYSTEM_TEMPLATE = "chat/default_system"


def _status_from_finish_reason(finish_reason: str) -> str:
    """RunResult.finish_reason → TurnRun 终态字符串."""
    if finish_reason == "error":
        return TERMINAL_ERROR
    if finish_reason == "aborted":
        return TERMINAL_ABORTED
    if finish_reason.startswith("partial"):
        return TERMINAL_PARTIAL
    return TERMINAL_OK


def _context_usage_event(snapshot) -> dict:
    """根据 ContextSnapshot.usage 构造前端可直接渲染的上下文占用事件 (含分层)。

    数据全部取自组装产物, 不触发任何额外 token 计算 / LLM 调用。
    """
    usage = snapshot.usage
    return {
        "type": "context_usage",
        "context_window": usage.context_window,
        "input_tokens": usage.total_input_tokens,
        "total_ratio": usage.total_ratio,
        "layers": [
            {
                "name": layer.name,
                "token_count": layer.token_count,
                "ratio": layer.ratio,
                "message_count": layer.message_count,
                "truncated": layer.truncated,
            }
            for layer in usage.layers
        ],
    }


class TurnOrchestrator:
    """无状态. start_turn / start_resume 创建并启动 ChatTurnRun.

    实际执行逻辑跑在 ChatTurnRun 的背景 task 里.
    """

    def __init__(self, context_manager: ContextManager) -> None:
        self._preparer = TurnPreparer()
        self._context_manager = context_manager
        self._finalizer = TurnFinalizer()
        self._resumer = TurnResumer()

    # ------------------------------------------------------------------
    # 新消息
    # ------------------------------------------------------------------
    async def start_turn(
        self,
        *,
        user_id: str,
        user_name: str,
        body: ChatCompletionIn,
        trace_id: str,
    ) -> ChatTurnRun:
        """同步 prepare (DB 占位写入) + 创建 TurnRun + 启动背景执行.

        立即返回 run, 路由层用 run.subscribe() 订阅事件流.
        """
        if trace_id:
            set_trace_id(trace_id)
        if user_id:
            set_user_id(user_id)

        # 1. 准备阶段必须同步完成 — message_id 是 SSE 协议头
        ctx = await self._preparer.prepare(
            user_id=user_id,
            user_name=user_name,
            session_id=body.session_id,
            message=body.message,
            trace_id=trace_id,
            model_options=body.model_options,
            attachments=body.attachments,
        )

        # 2. 建 run + 注册
        run = ChatTurnRun(
            message_id=ctx.assistant_msg_id,
            session_id=ctx.session_id,
            user_id=user_id,
            is_resume=False,
            trace_id=trace_id,
            on_unhandled_error=lambda message: self._finalizer.mark_unhandled_error(
                ctx, message
            ),
            on_cancelled=lambda message: self._finalizer.mark_cancelled(
                ctx, message
            ),
        )
        supervisor = get_chat_supervisor()
        await supervisor.register(run)

        # 3. 启动背景执行 (返回 task 但不 await)
        run.attach_task(self._execute_new_turn(run, ctx, body))
        logger.info(
            "TurnRun 已启动 (新消息) session=%s message_id=%s",
            ctx.session_id, ctx.assistant_msg_id,
        )
        return run

    # ------------------------------------------------------------------
    # 续写
    # ------------------------------------------------------------------
    async def start_resume(
        self,
        *,
        user_id: str,
        message_id: str,
        trace_id: str,
    ) -> ChatTurnRun:
        """续写: 加载 prev 状态 + 创建 TurnRun + 启动背景执行.

        若该 message_id 的 TurnRun 还在内存里且未终态, 不重复启动 (并发 resume 防护
        在 supervisor.register 里抛错; 路由层应当先 supervisor.get() 决定是直接订阅
        还是 start_resume).
        """
        if trace_id:
            set_trace_id(trace_id)
        if user_id:
            set_user_id(user_id)

        ctx, prev_state = await self._resumer.prepare(
            user_id=user_id, message_id=message_id, trace_id=trace_id,
        )

        run = ChatTurnRun(
            message_id=ctx.assistant_msg_id,
            session_id=ctx.session_id,
            user_id=user_id,
            is_resume=True,
            trace_id=trace_id,
            on_unhandled_error=lambda message: self._finalizer.mark_unhandled_error(
                ctx, message
            ),
            on_cancelled=lambda message: self._finalizer.mark_cancelled(
                ctx, message
            ),
        )
        # 关键: 把 baseline 设为 events.jsonl 当前 max seq,
        # subscribe 只下发本轮 resume 启动后产生的事件 (从 message_resumed 起),
        # 避免前端在已展示旧内容上再追加旧 delta 造成翻倍.
        run.baseline_seq = await run.store.initialize_seq()

        supervisor = get_chat_supervisor()
        try:
            await supervisor.register(run)
        except RuntimeError as exc:
            raise ResumeError("已有活跃流正在生成中，不允许并发 resume", code="40902") from exc

        run.attach_task(self._execute_resume(run, ctx, prev_state))
        logger.info(
            "TurnRun 已启动 (续写) session=%s message_id=%s prev_content_len=%d",
            ctx.session_id, ctx.assistant_msg_id, len(prev_state.prev_content),
        )
        return run

    # ==================================================================
    # 背景执行协程
    # ==================================================================
    async def _execute_new_turn(
        self,
        run: ChatTurnRun,
        ctx: TurnContext,
        body: ChatCompletionIn,
    ) -> str:
        """新对话的背景执行体. 返回终态字符串."""
        started_at = time.perf_counter()

        # 注入会话上下文 (供 write_file / read_file 工具定位会话沙盒 + 关联 chat_files)
        set_user_id(ctx.user_id)
        set_session_id(ctx.session_id)
        set_assistant_message_id(ctx.assistant_msg_id)
        # 本回合检索来源收集 (knowledge_search append, 回合末统一发 SSE + 落库)
        start_citation_collection()

        # 1. lifecycle 事件 (session_created / session_renamed / message_start)
        for ev in _lifecycle_events(ctx):
            await run.emit(ev.to_dict())

        # 2. context 组装 + 主动压缩
        request = ContextRequest(
            user_id=ctx.user_id,
            session_id=ctx.session_id,
            current_user_message=ctx.current_user_message,
            system_prompt_vars={"user_name": ctx.user_name},
            context_window=ctx.context_window,
            exclude_message_ids=tuple(ctx.exclude_message_ids),
        )

        async def on_compaction_started(pre_snapshot: ContextSnapshot) -> None:
            trigger_reason = (
                "history_truncated"
                if pre_snapshot.history_messages_dropped > 0
                else "approaching_window"
            )
            await run.emit({
                "type": "compaction_started",
                "reason": trigger_reason,
                "estimated_tokens": pre_snapshot.usage.total_input_tokens,
                "context_window": ctx.context_window,
            })

        async def on_compaction_done(
            result: CompactionResult,
            final_snapshot: ContextSnapshot,
        ) -> None:
            await run.emit({
                "type": "compaction_done",
                "tokens_saved": result.tokens_saved,
                "estimated_tokens": final_snapshot.usage.total_input_tokens,
                "rebuild_count": final_snapshot.rebuild_count,
                "ok": result.success,
            })

        try:
            allow_compaction = get_settings().memory.enabled
        except Exception:  # noqa: BLE001
            allow_compaction = False
        snapshot = await self._context_manager.build(
            request,
            allow_compaction=allow_compaction,
            on_compaction_started=on_compaction_started,
            on_compaction_done=on_compaction_done,
        )

        # 2.5 上下文占用快照 (分层) -- 复用组装产物, 不触发额外计算
        await run.emit(_context_usage_event(snapshot))

        # 3. 跑 agent
        runner = await self._setup_runner(
            ctx.agent_mode,
            snapshot.rendered_system_prompt,
            body.model_options.model_dump(),
            user_id=ctx.user_id,
        )
        tool_names: dict[str, str] = {}
        async for event in runner.run(ctx, snapshot.messages, run.abort_event):
            ed = event.to_dict()
            await run.emit(ed)
            # write_file 工具结果 → 后端主动下发 file_created (前端不解析工具内容)
            file_ev = _file_created_event(ed, tool_names)
            if file_ev is not None:
                await run.emit(file_ev)

        # 4. citations: 本回合 knowledge_search 命中的来源, 发 SSE + 落库
        citations = collected_citations()
        if citations:
            await run.emit({"type": "citations", "citations": citations})

        # 5. finalize (写 DB)
        final_event = await self._finalizer.finalize(
            ctx, runner.result, snapshot, citations=citations or None,
        )
        if final_event is not None:
            await run.emit(final_event.to_dict())

        logger.info(
            "TurnRun 执行完成 session=%s message_id=%s finish_reason=%s "
            "content_len=%d duration_ms=%.1f",
            ctx.session_id, ctx.assistant_msg_id, runner.result.finish_reason,
            len(runner.result.content or ""),
            (time.perf_counter() - started_at) * 1000,
        )
        return _status_from_finish_reason(runner.result.finish_reason)

    async def _execute_resume(
        self,
        run: ChatTurnRun,
        ctx: TurnContext,
        prev_state: ResumeState,
    ) -> str:
        """续写的背景执行体."""
        started_at = time.perf_counter()

        # 注入会话上下文 (供工具定位会话沙盒 + 关联 chat_files)
        set_user_id(ctx.user_id)
        set_session_id(ctx.session_id)
        set_assistant_message_id(ctx.assistant_msg_id)
        start_citation_collection()

        await run.emit({
            "type": "message_resumed",
            "message_id": ctx.assistant_msg_id,
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

        request = ContextRequest(
            user_id=ctx.user_id,
            session_id=ctx.session_id,
            current_user_message=ctx.current_user_message,
            system_prompt_vars={"user_name": ctx.user_name},
            context_window=ctx.context_window,
            exclude_message_ids=tuple(ctx.exclude_message_ids),
        )
        snapshot = await self._context_manager.build(
            request,
            allow_compaction=False,
        )
        await run.emit(_context_usage_event(snapshot))
        messages = _inject_partial_into_messages(snapshot.messages, prev_state)

        runner = await self._setup_runner(
            ctx.agent_mode,
            snapshot.rendered_system_prompt,
            None,
            user_id=ctx.user_id,
        )

        # 续写流实时去重: LLM 经常重复 prev_content 末尾几个字符 / 标点,
        # SSE delta 原样推前端会出现"前后割裂". 这里在 emit 之前剥掉重叠区,
        # 保证前端实时拼接与 finalizer 落库结果一致 (后者另走 strip_overlap).
        dedup = ResumeStreamDedup(prev_state.prev_content or "")
        tool_names: dict[str, str] = {}
        async for event in runner.run(ctx, messages, run.abort_event):
            if event.type == "delta":
                clean = dedup.feed(event.payload.get("content", ""))
                if not clean:
                    continue
                clean_payload = {**event.payload, "content": clean}
                await run.emit({"type": "delta", **clean_payload})
                continue
            ed = event.to_dict()
            await run.emit(ed)
            file_ev = _file_created_event(ed, tool_names)
            if file_ev is not None:
                await run.emit(file_ev)

        # flush: safety_buffer 里剩下的字符是确认非重叠的, 在结束时统一吐完.
        tail = dedup.flush()
        if tail:
            await run.emit({"type": "delta", "content": tail})

        citations = collected_citations()
        if citations:
            await run.emit({"type": "citations", "citations": citations})

        final_event = await self._finalizer.finalize(
            ctx, runner.result, snapshot, prev_state=prev_state,
            citations=citations or None,
        )
        if final_event is not None:
            await run.emit(final_event.to_dict())

        logger.info(
            "TurnRun 续写完成 session=%s message_id=%s finish_reason=%s "
            "delta_content_len=%d dedup_stripped=%d duration_ms=%.1f",
            ctx.session_id, ctx.assistant_msg_id, runner.result.finish_reason,
            len(runner.result.content or ""),
            dedup.dedup_chars,
            (time.perf_counter() - started_at) * 1000,
        )
        return _status_from_finish_reason(runner.result.finish_reason)

    # ==================================================================
    # 辅助
    # ==================================================================
    async def _setup_runner(
        self,
        agent_mode: str,
        system_prompt: str,
        model_options: dict | None,
        *,
        user_id: str | None = None,
    ) -> ReActRunner:
        settings = get_settings()
        provider = model_options.get("provider") if model_options else None
        model = model_options.get("model") if model_options else None
        profile = get_agent_profile(agent_mode)
        binding = GatewayBinding(
            gateway=get_llm_gateway(settings),
            user_id=user_id,
            preferred_provider=provider,
            preferred_model=model,
            model_profile=profile.model_profile,  # web 无 pin 时退到档位链
            task_type="chat",
        )
        llm = GatewayLLMAdapter(binding)
        return ReActRunner.from_profile(
            llm, profile, system_prompt=system_prompt,
        )


# ---------------------------------------------------------------------------
# 模块级辅助
# ---------------------------------------------------------------------------
def _file_created_event(ed: dict, tool_names: dict[str, str]) -> dict | None:
    """识别 write_file 工具结果, 构造 file_created 事件。

    后端主动下发文件元数据 (前端只认 file_created, 不解析 tool_call/tool_result 内容)。
    tool_names 在一次 turn 内累积 tool_call_id → tool_name 映射。
    """
    etype = ed.get("type")
    if etype == "tool_call":
        tc = ed.get("tool_call") or {}
        tcid = tc.get("id")
        if tcid:
            tool_names[tcid] = tc.get("tool_name") or tc.get("tool_id") or ""
        return None
    if etype != "tool_result" or ed.get("status") != "success":
        return None
    if tool_names.get(ed.get("tool_call_id", "")) != "write_file":
        return None
    try:
        data = json.loads(ed.get("result") or "")
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("ok") or not data.get("id"):
        return None
    name = data.get("path") or data.get("filename") or "file"
    return {
        "type": "file_created",
        "id": str(data["id"]),
        "name": name,
        "source": "generated",
        "size_bytes": data.get("size_bytes", 0),
        "mime_type": data.get("mime_type"),
    }


def _lifecycle_events(ctx: TurnContext):
    """生成 session_created / session_renamed / message_start 事件."""
    if ctx.is_new_session:
        yield AgentEvent("session_created", {
            "session_id": ctx.session_id,
            "title": ctx.new_title or "新会话",
        })
    elif ctx.new_title:
        yield AgentEvent("session_renamed", {
            "session_id": ctx.session_id,
            "title": ctx.new_title,
        })
    yield AgentEvent("message_start", {
        "message_id": ctx.assistant_msg_id,
        "session_id": ctx.session_id,
    })


def _inject_partial_into_messages(
    base_messages: list[Message], prev_state: ResumeState,
) -> list[Message]:
    """把上次的 partial assistant + 完成态 tool 结果插入 messages 末尾.

    与旧 orchestrator 行为一致.
    """
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
# 工厂 + 兼容导出
# ---------------------------------------------------------------------------
def build_turn_orchestrator() -> TurnOrchestrator:
    async def build_once(request: ContextRequest) -> ContextSnapshot:
        from forge.chat.tools import resolve_chat_tools

        tools_meta = [
            {"name": tool.name, "description": tool.description}
            for tool in resolve_chat_tools()
        ]
        kb_list = await fetch_kb_list(request.user_id)
        system_prompt = get_registry().render(
            _CHAT_SYSTEM_TEMPLATE,
            user_system_prompt="",
            user_name=str(request.system_prompt_vars.get("user_name", "")),
            datetime=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            tools=tools_meta,
            kb_list=kb_list,
        )
        build_request = replace(request, system_prompt_override=system_prompt)

        async with session_scope() as db:
            builder = build_context_builder(
                message_store=ChatMessageRepository(db),
                memory_store=get_memory_store(),
            )
            snapshot = await builder.build(build_request)

        logger.info(
            "上下文构建完成 session=%s messages=%d est_input_tokens=%d "
            "history_used=%d history_dropped=%d facts=%d summary=%s degraded=%s",
            request.session_id, len(snapshot.messages),
            snapshot.usage.total_input_tokens,
            snapshot.history_messages_used,
            snapshot.history_messages_dropped,
            snapshot.facts_included,
            snapshot.summary_included,
            snapshot.degraded or "[]",
        )
        if snapshot.degraded:
            logger.warning(
                "上下文构建有降级 session=%s reasons=%s",
                request.session_id, snapshot.degraded,
            )
        return snapshot

    context_manager = ContextManager(
        build_once,
        CompactionController(SummaryCompaction(), ThresholdTrigger()),
    )
    return TurnOrchestrator(context_manager)


__all__ = [
    "TurnOrchestrator",
    "build_turn_orchestrator",
]
