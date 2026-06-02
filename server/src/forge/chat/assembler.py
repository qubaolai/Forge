"""ContextAssembler: 上下文组装 + 主动压缩。

直连 context_mgmt 新内核 (无 compat 适配层):
    1. 渲染 system_prompt (Jinja2 模板 + tools + KB list)
    2. 调 DefaultContextBuilder.build() 拼最终 messages -> ContextSnapshot
    3. 主动压缩: 检测 token 压力 -> CompactionController -> 重建上下文

输出统一为 ContextSnapshot (含 messages / usage(分层用量) / degraded 等)。
"""

from __future__ import annotations

import logging
from datetime import datetime

from forge.chat.kb_resolver import fetch_kb_list
from forge.chat.types import TurnContext
from forge.config.settings import get_settings
from forge.context_mgmt.builder.factory import build_context_builder
from forge.context_mgmt.compaction.controller import CompactionController
from forge.context_mgmt.compaction.strategies.summary import SummaryCompaction
from forge.context_mgmt.compaction.trigger.threshold import ThresholdTrigger
from forge.context_mgmt.memory_factory import get_memory_store
from forge.context_mgmt.types import ContextMode, ContextRequest, ContextSnapshot
from forge.infrastructure.database.database import get_session_factory
from forge.infrastructure.database.repositories.chat_message_repo import ChatMessageRepository
from forge.observability.tracing.tracer import span
from forge.prompts import get_registry

logger = logging.getLogger(__name__)


class ContextAssembler:
    """无状态。每次 assemble() 自有 DB session。"""

    SYSTEM_TEMPLATE = "chat/default_system"
    DEFAULT_COMPACTION_THRESHOLD: float = 0.85

    def __init__(self, compaction_threshold: float | None = None) -> None:
        self._threshold = compaction_threshold or self.DEFAULT_COMPACTION_THRESHOLD
        # 触发器 + 压缩控制器 (长寿, 无状态)
        self._trigger = ThresholdTrigger(self._threshold)
        self._controller = CompactionController(SummaryCompaction(), self._trigger)

    # ------------------------------------------------------------------
    # 主入口: 初次组装
    # ------------------------------------------------------------------
    async def assemble(self, ctx: TurnContext) -> tuple[ContextSnapshot, str]:
        with span(
            "chat.assemble",
            session_id=ctx.session_id,
            context_window=ctx.context_window,
        ) as s:
            system_prompt = await self._render_system_prompt(ctx)
            snapshot = await self._build_once(ctx, system_prompt)
            s.set("estimated_input_tokens", snapshot.usage.total_input_tokens)
            s.set("history_used", snapshot.history_messages_used)
            s.set("history_dropped", snapshot.history_messages_dropped)
            s.set("summary_included", snapshot.summary_included)
            s.set("facts_included", snapshot.facts_included)
            if snapshot.degraded:
                s.set("degraded", list(snapshot.degraded))
        return snapshot, system_prompt

    # ------------------------------------------------------------------
    # 压缩判定
    # ------------------------------------------------------------------
    def should_compact(self, snapshot: ContextSnapshot, ctx: TurnContext) -> bool:
        try:
            if not get_settings().memory.enabled:
                return False
        except Exception:  # noqa: BLE001
            return False
        return self._trigger.should_compact(snapshot)

    # ------------------------------------------------------------------
    # 压缩 + 重建
    # ------------------------------------------------------------------
    async def compact_and_reassemble(
        self, ctx: TurnContext, prev: ContextSnapshot,
    ) -> tuple[ContextSnapshot, str, int]:
        prev_tokens = prev.usage.total_input_tokens
        with span(
            "chat.compact",
            session_id=ctx.session_id,
            pre_tokens=prev_tokens,
            context_window=ctx.context_window,
            history_dropped=prev.history_messages_dropped,
        ) as s:
            result = await self._controller.compact_now(ctx.session_id, prev)
            if not result.success:
                logger.warning(
                    "inline 压缩失败 session=%s: %s -- 回退到压缩前的上下文",
                    ctx.session_id, result.failure_reason,
                )
                prev.degraded.append("compaction_failed")
                s.set("ok", False)
                s.set("failure", result.failure_reason or "compaction_failed")
                return prev, "", 0

            system_prompt = await self._render_system_prompt(ctx)
            new_snapshot = await self._build_once(ctx, system_prompt)

            new_snapshot.compaction_performed = True
            new_snapshot.rebuild_count = (prev.rebuild_count or 0) + 1
            tokens_saved = max(0, prev_tokens - new_snapshot.usage.total_input_tokens)
            new_snapshot.compaction_token_saved = tokens_saved
            s.set("ok", True)
            s.set("post_tokens", new_snapshot.usage.total_input_tokens)
            s.set("tokens_saved", tokens_saved)
            s.set("rebuild_count", new_snapshot.rebuild_count)
        new_snapshot.degraded.append("active_compaction_triggered")

        logger.info(
            "主动压缩完成 session=%s tokens %d -> %d (节省 %d)",
            ctx.session_id, prev_tokens,
            new_snapshot.usage.total_input_tokens, tokens_saved,
        )
        return new_snapshot, system_prompt, tokens_saved

    # ------------------------------------------------------------------
    # 内部: 调 DefaultContextBuilder.build 一次
    # ------------------------------------------------------------------
    async def _build_once(self, ctx: TurnContext, system_prompt: str) -> ContextSnapshot:
        factory = get_session_factory()
        async with factory() as db:
            msg_repo = ChatMessageRepository(db)
            builder = build_context_builder(
                mode=ContextMode.CHAT,
                message_store=msg_repo,
                memory_store=get_memory_store(),
            )
            snapshot = await builder.build(
                ContextRequest(
                    user_id=ctx.user_id,
                    session_id=ctx.session_id,
                    current_user_message=ctx.current_user_message,
                    mode=ContextMode.CHAT,
                    system_prompt_override=system_prompt,
                    context_window=ctx.context_window,
                    exclude_message_ids=tuple(ctx.exclude_message_ids),
                )
            )
        logger.info(
            "上下文构建完成 session=%s messages=%d est_input_tokens=%d "
            "history_used=%d history_dropped=%d facts=%d summary=%s degraded=%s",
            ctx.session_id, len(snapshot.messages),
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
                ctx.session_id, snapshot.degraded,
            )
        return snapshot

    # ------------------------------------------------------------------
    # 内部: system prompt 渲染
    # ------------------------------------------------------------------
    async def _render_system_prompt(self, ctx: TurnContext) -> str:
        from forge.chat.tools import resolve_chat_tools

        tools_meta = [
            {"name": t.name, "description": t.description}
            for t in resolve_chat_tools(get_settings())
        ]
        kb_list = await fetch_kb_list(ctx.user_id)
        return get_registry().render(
            self.SYSTEM_TEMPLATE,
            user_system_prompt="",
            user_name=ctx.user_name,
            datetime=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            tools=tools_meta,
            kb_list=kb_list,
        )
