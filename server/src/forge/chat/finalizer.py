"""TurnFinalizer: 把 RunResult 落地 + 发终态 SSE 事件.

职责:
    1. 根据 finish_reason 决定 assistant message 的最终状态
    2. 写 DB (content / status / tool_calls / usage / context_meta / reasoning_*)
    3. 产出终态 SSE 事件 (done / error / task_partial)
    4. 正常完成时 publish "turn.completed" 事件
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from forge.agents.base import AgentEvent
from forge.chat.types import ResumeState, RunResult, TurnContext
from forge.context_mgmt.types import ContextSnapshot
from forge.core.content_merge import strip_overlap
from forge.infrastructure.database.database import get_session_factory
from forge.infrastructure.database.repositories.chat_message_repo import ChatMessageRepository
from forge.observability.tracing.tracer import span

logger = logging.getLogger(__name__)


class TurnFinalizer:
    """无状态. 每次 finalize() 自有 DB 事务."""

    async def finalize(
        self,
        ctx: TurnContext,
        result: RunResult,
        snapshot: ContextSnapshot,
        *,
        prev_state: ResumeState | None = None,
    ) -> AgentEvent | None:
        """落库 + 发终态事件 + 发布 turn.completed。返回终态事件，调用方负责 yield。"""
        with span(
            "chat.finalize",
            session_id=ctx.session_id,
            assistant_msg_id=ctx.assistant_msg_id,
            is_resume=prev_state is not None,
        ) as s:
            if prev_state is not None:
                result = self._merge_with_prev(result, prev_state)

            status = self._status_for(result.finish_reason)
            await self._update_message(ctx, result, snapshot, status)

            s.set("finish_reason", result.finish_reason)
            s.set("status", status)
            s.set("content_len", len(result.content or ""))
            s.set("tool_calls", len(result.tool_calls or []))
            s.set("total_tokens", (result.usage or {}).get("total_tokens", 0))

            logger.info(
                "终态落库 session=%s message_id=%s status=%s finish_reason=%s "
                "content_len=%d tool_calls=%d usage=%s resumed=%s",
                ctx.session_id, ctx.assistant_msg_id, status, result.finish_reason,
                len(result.content or ""), len(result.tool_calls or []),
                result.usage or {}, prev_state is not None,
            )

            # 终态事件
            if result.finish_reason == "error":
                event = self._error_event(ctx, result)
            elif result.is_resumable:
                event = self._partial_event(ctx, result)
            else:
                event = self._done_event(ctx, result)

            if result.is_terminal_ok:
                await self._publish_turn_completed(ctx)
                s.set("turn_completed_published", True)

            return event

    # ------------------------------------------------------------------
    # Resume 合并: 用 longest suffix-prefix match 修复 LLM 续写重复问题
    # ------------------------------------------------------------------
    @classmethod
    def _merge_with_prev(cls, result: RunResult, prev: ResumeState) -> RunResult:
        """Resume 合并: 新生成的内容追加到上次的内容上 (含 overlap strip).

        Note:
            - content: prev + strip_overlap(new) -- 避免 LLM 续写重复
            - finish_reason 用新值 (本轮的结果)
            - reasoning_duration_ms 是累计墙钟, 直接相加
            - usage 各 token 项相加
        """
        merged_usage: dict = {}
        for src in (prev.prev_usage, result.usage):
            for k, v in (src or {}).items():
                if not isinstance(v, int | float):
                    continue
                merged_usage[k] = merged_usage.get(k, 0) + v

        stripped_new_content, overlap_chars = strip_overlap(
            prev.prev_content or "", result.content or ""
        )
        if overlap_chars > 0:
            logger.info(
                "续写去重 剥离字符=%d prev_tail=%r new_head=%r",
                overlap_chars,
                (prev.prev_content or "")[-min(40, overlap_chars + 10) :],
                (result.content or "")[: min(40, overlap_chars + 10)],
            )

        return RunResult(
            finish_reason=result.finish_reason,
            content=(prev.prev_content or "") + stripped_new_content,
            tool_calls=list(prev.prev_tool_calls or []) + list(result.tool_calls or []),
            reasoning_content=(
                (prev.prev_reasoning_content or "") + (result.reasoning_content or "")
            )
            or None,
            reasoning_duration_ms=(
                (prev.prev_reasoning_duration_ms or 0) + (result.reasoning_duration_ms or 0)
            )
            or None,
            usage=merged_usage,
            error_message=result.error_message,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _status_for(finish_reason: str) -> str:
        """finish_reason -> DB status.

        状态:
            done     - 最终完成, 不可继续
            error    - 不可恢复, 不可继续
            aborted  - 用户主动停, 可继续 (/chat/resume)
            partial  - 系统软上限触发, 可继续
        """
        if finish_reason == "error":
            return "error"
        if finish_reason == "aborted":
            return "aborted"
        if finish_reason.startswith("partial_"):
            return "partial"
        return "done"

    # ------------------------------------------------------------------
    async def _update_message(
        self,
        ctx: TurnContext,
        result: RunResult,
        snapshot: ContextSnapshot,
        status: str,
    ) -> None:
        # assistant 消息 token 数落库算一次 (供上下文组装热路径读, 免重复 tiktoken)
        from forge.context_mgmt.meter.token_meter import get_token_meter

        token_count = (
            get_token_meter().count_text(result.content) if result.content else None
        )

        factory = get_session_factory()
        async with factory() as db:
            repo = ChatMessageRepository(db)
            msg = await repo.get_by_id(ctx.assistant_msg_id)
            if msg:
                await repo.update(
                    msg,
                    content=result.content,
                    status=status,
                    tool_calls=result.tool_calls or None,
                    usage=result.usage or None,
                    context_meta=self._build_context_meta(ctx, result, snapshot),
                    reasoning_content=result.reasoning_content,
                    reasoning_duration_ms=result.reasoning_duration_ms,
                    error_message=result.error_message,
                    token_count=token_count,
                )
            await db.commit()

    @staticmethod
    def _build_context_meta(
        ctx: TurnContext,
        result: RunResult,
        snapshot: ContextSnapshot,
    ) -> dict:
        """构造落库元信息：上下文占用(分层) + resume 恢复所需字段。

        - 上下文占用 (estimated_input_tokens / context_window / layers) 供前端展示;
        - model_options / finish_reason 供 resume 恢复模型与状态 (resumer 读取)。
        """
        usage = snapshot.usage
        meta: dict = {
            # ---- 上下文占用 (Task1: 分层) ----
            "estimated_input_tokens": usage.total_input_tokens,
            "context_window": usage.context_window,
            "max_output_tokens": usage.max_output_tokens,
            "total_ratio": usage.total_ratio,
            "layers": [asdict(layer) for layer in usage.layers],
            # ---- 历史 / 降级统计 ----
            "history_messages_used": snapshot.history_messages_used,
            "history_messages_dropped": snapshot.history_messages_dropped,
            "summary_included": snapshot.summary_included,
            "facts_included": snapshot.facts_included,
            "compaction_performed": snapshot.compaction_performed,
            "rebuild_count": snapshot.rebuild_count,
            "degraded": list(snapshot.degraded),
            # 非降级信息标记 (如 digest_substituted 无损折叠为引用), 供前端展示
            "info": list(snapshot.info),
            # ---- resume 恢复用 ----
            "finish_reason": result.finish_reason,
        }
        if ctx.model_options:
            meta["model_options"] = dict(ctx.model_options)
        return meta

    async def mark_unhandled_error(self, ctx: TurnContext, message: str) -> None:
        """执行链路未进入 finalize 时，将 assistant 消息兜底标记为 error。"""
        await self._mark_terminal_without_result(
            ctx,
            status="error",
            finish_reason="error",
            error_message=message or "未知错误",
        )

    async def mark_cancelled(self, ctx: TurnContext, message: str) -> None:
        """服务关闭硬取消时，将 assistant 消息兜底标记为 aborted。"""
        await self._mark_terminal_without_result(
            ctx,
            status="aborted",
            finish_reason="aborted",
            error_message=message or "服务关闭，生成已中断",
        )

    async def _mark_terminal_without_result(
        self,
        ctx: TurnContext,
        *,
        status: str,
        finish_reason: str,
        error_message: str,
    ) -> None:
        factory = get_session_factory()
        async with factory() as db:
            repo = ChatMessageRepository(db)
            msg = await repo.get_by_id(ctx.assistant_msg_id)
            if msg:
                meta = dict(msg.context_meta or {})
                meta["finish_reason"] = finish_reason
                meta["unhandled_terminal"] = True
                if ctx.model_options:
                    meta["model_options"] = dict(ctx.model_options)
                await repo.update(
                    msg,
                    status=status,
                    error_message=error_message,
                    context_meta=meta,
                )
            await db.commit()
        logger.info(
            "异常终态兜底落库 session=%s message_id=%s status=%s finish_reason=%s",
            ctx.session_id,
            ctx.assistant_msg_id,
            status,
            finish_reason,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _done_event(ctx: TurnContext, result: RunResult) -> AgentEvent:
        return AgentEvent(
            "done",
            {
                "usage": {
                    "prompt_tokens": result.usage.get("prompt_tokens", 0),
                    "completion_tokens": result.usage.get("completion_tokens", 0),
                    "total_tokens": result.usage.get("total_tokens", 0),
                },
                "finish_reason": result.finish_reason,
                "reasoning_duration_ms": result.reasoning_duration_ms,
            },
        )

    @staticmethod
    def _error_event(ctx: TurnContext, result: RunResult) -> AgentEvent:
        return AgentEvent(
            "error",
            {
                "message": result.error_message or "未知错误",
                "content": result.content,
                "tool_calls": result.tool_calls or None,
                "usage": result.usage or {},
            },
        )

    @staticmethod
    def _partial_event(ctx: TurnContext, result: RunResult) -> AgentEvent:
        """task_partial: 未完成但可继续.

        前端拿到这事件应该:
            - 关闭打字光标
            - 显示 [继续生成] 按钮
            - 点击后 POST /chat/resume {message_id} (R6 实现)
        """
        return AgentEvent(
            "task_partial",
            {
                "message_id": ctx.assistant_msg_id,
                "session_id": ctx.session_id,
                "reason": result.finish_reason,
                "content_so_far": result.content,
                "tool_calls_so_far": result.tool_calls or None,
                "usage": result.usage or {},
                "reasoning_duration_ms": result.reasoning_duration_ms,
                "resumable": True,
            },
        )

    # ------------------------------------------------------------------
    async def _publish_turn_completed(self, ctx: TurnContext) -> None:
        """异步发布事件总线, 失败仅日志不抛."""
        try:
            from forge.infrastructure.event_bus import get_event_bus

            await get_event_bus().publish(
                "turn.completed",
                {
                    "session_id": ctx.session_id,
                    "user_id": ctx.user_id,
                    "trace_id": ctx.trace_id,
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("发布 turn.completed 失败")
