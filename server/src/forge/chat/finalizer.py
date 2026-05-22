"""TurnFinalizer: 把 RunResult 落地 + 发终态 SSE 事件.

职责:
    1. 根据 finish_reason 决定 assistant message 的最终状态
    2. 写 DB (content / status / tool_calls / usage / context_meta / reasoning_*)
    3. 产出终态 SSE 事件 (done / error / task_partial)
    4. 正常完成时 publish "turn.completed" 事件
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import asdict

from forge.agents.base import AgentEvent
from forge.chat.types import ResumeState, RunResult, TurnContext
from forge.context.base import BuildMeta
from forge.infrastructure.database.database import get_session_factory

# Storage protocol injected, swap by deployment_mode (S6.5 M3).
from forge.infrastructure.storage import make_message_store
from forge.observability.tracing.tracer import span

logger = logging.getLogger(__name__)


class TurnFinalizer:
    """无状态. 每次 finalize() 自有 DB 事务."""

    async def finalize(
        self,
        ctx: TurnContext,
        result: RunResult,
        build_meta: BuildMeta,
        *,
        prev_state: ResumeState | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """落库 + 发终态事件 + 发布 turn.completed.

        AsyncIterator: 单事件 yield 一次, 走 generator 接口让 Orchestrator
        统一 `async for` 透传.

        三条终态分支:
            stop        -> done       事件, status=done,    publish turn.completed
            aborted     -> task_partial 事件, status=aborted, 不 publish (用户可能继续)
            partial_*   -> task_partial 事件, status=partial,  不 publish (同上)
            error       -> error      事件, status=error,    不 publish

        Resume 场景 (prev_state 非空):
            - 把 result.content 追加到 prev_state.prev_content (不替换)
            - tool_calls / reasoning / usage 都累加
            - 写库时是合并后的全量, 跟普通 turn 一致
        """
        with span(
            "chat.finalize",
            session_id=ctx.session_id,
            assistant_msg_id=ctx.assistant_msg_id,
            is_resume=prev_state is not None,
            workspace_id=ctx.workspace_id,
            workflow_id=ctx.workflow_id,
        ) as s:
            # 0. resume 合并 (resume 时 result 只是本次新增, 不是全量; 合并出全量)
            if prev_state is not None:
                result = self._merge_with_prev(result, prev_state)

            # 1. DB 状态
            status = self._status_for(result.finish_reason)

            # 2. DB 更新
            await self._update_message(ctx, result, build_meta, status)

            s.set("finish_reason", result.finish_reason)
            s.set("status", status)
            s.set("content_len", len(result.content or ""))
            s.set("tool_calls", len(result.tool_calls or []))
            s.set("total_tokens", (result.usage or {}).get("total_tokens", 0))

            logger.info(
                "终态落库 session=%s message_id=%s status=%s finish_reason=%s "
                "content_len=%d tool_calls=%d usage=%s resumed=%s",
                ctx.session_id,
                ctx.assistant_msg_id,
                status,
                result.finish_reason,
                len(result.content or ""),
                len(result.tool_calls or []),
                result.usage or {},
                prev_state is not None,
            )

            # 3. 发终态事件
            if result.finish_reason == "error":
                yield self._error_event(ctx, result)
            elif result.is_resumable:
                yield self._partial_event(ctx, result)
            else:
                yield self._done_event(ctx, result)

            # 4. publish turn.completed -- 仅在 LLM 真正自然完成时
            if result.is_terminal_ok:
                await self._publish_turn_completed(ctx)
                s.set("turn_completed_published", True)

    # ------------------------------------------------------------------
    # Resume 合并: 关键 = strip suffix-prefix overlap (修 LLM 续写重复问题)
    # ------------------------------------------------------------------
    # 续写重复 (continuation overlap / resume duplication) 修复:
    # LLM 在 resume 场景下经常重复 prev_content 末尾的一小段, 尤其在代码块 / 表格
    # 截断处. 即使 prompt 里明确告知 "不要重复", 失误率仍不为零.
    # 服务端在 _merge_with_prev 里做一次 longest suffix-prefix match, 自动 strip
    # 重叠. 这是不依赖 LLM 配合的兜底.
    OVERLAP_MAX = 256  # 最长检测重叠字符数 (取末尾这么多比对); 算法 O(min(N, 256))

    @staticmethod
    def _strip_overlap(prev: str, new: str, max_overlap: int = OVERLAP_MAX) -> tuple[str, int]:
        """找 prev 的最长后缀, 它同时是 new 的前缀; 返回 (剥离后的 new, 重叠字符数).

        prev = "...def foo():\\n    return"
        new  = "    return 42\\n```"
        ->   ("42\\n```", 10)   # "    return" 被识别为重叠

        若 prev/new 任一为空 -> 直接返回原 new + 0.
        若没找到任何重叠 -> 返回原 new + 0.

        算法: 从可能的最长重叠 (= min(len(prev), len(new), max_overlap)) 向下试,
        命中即返回, 保证拿到的是最长匹配.
        """
        if not prev or not new:
            return new, 0
        end = min(len(prev), len(new), max_overlap)
        for k in range(end, 0, -1):
            if prev.endswith(new[:k]):
                return new[k:], k
        return new, 0

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

        stripped_new_content, overlap_chars = cls._strip_overlap(
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
            aborted  - 用户主动停, 可继续 (R6 /chat/resume)
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
        build_meta: BuildMeta,
        status: str,
    ) -> None:
        factory = get_session_factory()
        async with factory() as db:
            repo = make_message_store(db)
            msg = await repo.get_by_id(ctx.assistant_msg_id)
            if msg:
                await repo.update(
                    msg,
                    content=result.content,
                    status=status,
                    tool_calls=result.tool_calls or None,
                    usage=result.usage or None,
                    context_meta=asdict(build_meta),
                    reasoning_content=result.reasoning_content,
                    reasoning_duration_ms=result.reasoning_duration_ms,
                    error_message=result.error_message,
                )
            await db.commit()

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
                    "workspace_id": ctx.workspace_id,
                    "workflow_id": ctx.workflow_id,
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("发布 turn.completed 失败")
