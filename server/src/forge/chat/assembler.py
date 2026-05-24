"""ContextAssembler: 上下文组装 + 主动压缩。

职责:
    1. 渲染 system_prompt (Jinja2 模板 + tools + KB list)
    2. 调 ContextBuilder.build() 拼最终 messages
    3. 主动压缩: 检测 token 压力 → 触发 SummaryService → 重建上下文
"""

from __future__ import annotations

import logging
from datetime import datetime

from forge.config.settings import get_settings

from forge.chat.kb_resolver import fetch_kb_list
from forge.chat.types import TurnContext
from forge.context.base import (
    AgentContextConfig,
    AssembledContext,
    BuildRequest,
)
from forge.context.factory import build_context_builder
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

    # ------------------------------------------------------------------
    # 主入口: 初次组装
    # ------------------------------------------------------------------
    async def assemble(self, ctx: TurnContext) -> tuple[AssembledContext, str]:
        with span(
            "chat.assemble",
            session_id=ctx.session_id,
            context_window=ctx.context_window,
        ) as s:
            system_prompt = await self._render_system_prompt(ctx)
            result = await self._build_once(ctx, system_prompt)
            s.set("estimated_input_tokens", result.meta.estimated_input_tokens)
            s.set("history_used", result.meta.history_messages_used)
            s.set("history_dropped", result.meta.history_messages_dropped)
            s.set("summary_included", result.meta.summary_included)
            s.set("facts_included", result.meta.facts_included)
            if result.meta.degraded:
                s.set("degraded", list(result.meta.degraded))
        return result, system_prompt

    # ------------------------------------------------------------------
    # 压缩判定
    # ------------------------------------------------------------------
    def should_compact(self, result: AssembledContext, ctx: TurnContext) -> bool:
        try:
            if not get_settings().memory.enabled:
                return False
        except Exception:  # noqa: BLE001
            return False

        if result.meta.history_messages_dropped > 0:
            return True
        if ctx.context_window > 0:
            ratio = result.meta.estimated_input_tokens / ctx.context_window
            if ratio > self._threshold:
                return True
        return False

    # ------------------------------------------------------------------
    # 压缩 + 重建
    # ------------------------------------------------------------------
    async def compact_and_reassemble(
        self, ctx: TurnContext, prev: AssembledContext,
    ) -> tuple[AssembledContext, str, int]:
        from forge.memory.summary.service import (
            InfrastructureError,
            get_summary_service,
        )

        prev_tokens = prev.meta.estimated_input_tokens
        with span(
            "chat.compact",
            session_id=ctx.session_id,
            pre_tokens=prev_tokens,
            context_window=ctx.context_window,
            history_dropped=prev.meta.history_messages_dropped,
        ) as s:
            try:
                await get_summary_service().summarize_session(
                    ctx.session_id, workspace_id=None,
                )
            except InfrastructureError as exc:
                logger.warning(
                    "inline 压缩失败 session=%s: %s -- 回退到压缩前的上下文",
                    ctx.session_id, exc,
                )
                prev.meta.degraded.append("compaction_failed")
                s.set("ok", False)
                s.set("failure", "summarize_service_error")
                s.set_error(exc)
                return prev, "", 0
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "inline 压缩崩溃 session=%s -- 回退到压缩前的上下文",
                    ctx.session_id,
                )
                prev.meta.degraded.append("compaction_failed")
                s.set("ok", False)
                s.set("failure", "unexpected_error")
                s.set_error(exc)
                return prev, "", 0

            system_prompt = await self._render_system_prompt(ctx)
            new_result = await self._build_once(ctx, system_prompt)

            new_result.meta.compaction_performed = True
            new_result.meta.rebuild_count = (prev.meta.rebuild_count or 0) + 1
            tokens_saved = max(0, prev_tokens - new_result.meta.estimated_input_tokens)
            new_result.meta.compaction_token_saved = tokens_saved
            s.set("ok", True)
            s.set("post_tokens", new_result.meta.estimated_input_tokens)
            s.set("tokens_saved", tokens_saved)
            s.set("rebuild_count", new_result.meta.rebuild_count)
        new_result.meta.degraded.append("active_compaction_triggered")

        logger.info(
            "主动压缩完成 session=%s tokens %d -> %d (节省 %d)",
            ctx.session_id, prev_tokens,
            new_result.meta.estimated_input_tokens, tokens_saved,
        )
        return new_result, system_prompt, tokens_saved

    # ------------------------------------------------------------------
    # 内部: 调 ContextBuilder.build 一次
    # ------------------------------------------------------------------
    async def _build_once(self, ctx: TurnContext, system_prompt: str) -> AssembledContext:
        factory = get_session_factory()
        async with factory() as db:
            msg_repo = ChatMessageRepository(db)
            builder = build_context_builder(msg_repo)
            result = await builder.build(
                BuildRequest(
                    user_id=ctx.user_id,
                    session_id=ctx.session_id,
                    current_user_message=ctx.current_user_message,
                    agent=AgentContextConfig(
                        system_prompt=system_prompt,
                        context_window=ctx.context_window,
                    ),
                    exclude_message_ids=ctx.exclude_message_ids,
                )
            )
        logger.info(
            "上下文构建完成 session=%s messages=%d est_input_tokens=%d "
            "history_used=%d history_dropped=%d facts=%d summary=%s degraded=%s",
            ctx.session_id, len(result.messages),
            result.meta.estimated_input_tokens,
            result.meta.history_messages_used,
            result.meta.history_messages_dropped,
            result.meta.facts_included,
            result.meta.summary_included,
            result.meta.degraded or "[]",
        )
        if result.meta.degraded:
            logger.warning(
                "上下文构建有降级 session=%s reasons=%s",
                ctx.session_id, result.meta.degraded,
            )
        return result

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
