"""DefaultContextBuilder: Fork-Join 并行的上下文构建器.

流程:
    1. BudgetPolicy 分配 WindowBudget
    2. Fork: 并行执行 PromptRenderer.render() + ContentGatherer.gather()
    3. Join: MessageAssembler.assemble() 串行组装最终 messages + 用量

无状态, 同一实例可并发处理多个请求.
"""

from __future__ import annotations

import asyncio
import logging

from forge.context_mgmt.builder.content_gatherer import ContentGatherer
from forge.context_mgmt.builder.message_assembler import MessageAssembler
from forge.context_mgmt.builder.prompt_renderer import PromptRenderer
from forge.context_mgmt.protocols import BudgetPolicy
from forge.context_mgmt.types import ContextRequest, ContextSnapshot
from forge.observability.tracing.tracer import span

logger = logging.getLogger(__name__)


class DefaultContextBuilder:
    """Fork-Join 并行的上下文构建器."""

    def __init__(
        self,
        renderer: PromptRenderer,
        gatherer: ContentGatherer,
        assembler: MessageAssembler,
        budget_policy: BudgetPolicy,
    ) -> None:
        self._renderer = renderer
        self._gatherer = gatherer
        self._assembler = assembler
        self._budget_policy = budget_policy

    async def build(self, request: ContextRequest) -> ContextSnapshot:
        with span(
            "context.build",
            caller=request.caller,
            mode=request.mode.value,
            session_id=request.session_id,
            user_id=request.user_id,
            context_window=request.context_window,
            history_limit=request.history_limit,
        ) as s:
            budget = self._budget_policy.allocate(request)

            # Fork: 两路并行
            rendered_prompt, gather_result = await asyncio.gather(
                self._renderer.render(request),
                self._gatherer.gather(request),
            )

            # Join: 串行组装
            snapshot = self._assembler.assemble(
                request, rendered_prompt, gather_result, budget
            )

            # span 写入用量元信息 (供 LoggingTracer / OTel / Langfuse 用)
            # 字段名对齐 BuildMeta, 避免 trace 工具因字段名变化而失效.
            s.set("estimated_input_tokens", snapshot.usage.total_input_tokens)
            s.set("max_output_tokens", snapshot.usage.max_output_tokens)
            s.set("usage_ratio", snapshot.usage_ratio)
            s.set("history_messages_used", snapshot.history_messages_used)
            s.set("history_messages_dropped", snapshot.history_messages_dropped)
            s.set("history_messages_filtered", snapshot.history_messages_filtered)
            s.set("summary_included", snapshot.summary_included)
            s.set("facts_included", snapshot.facts_included)
            s.set("degraded", list(snapshot.degraded))

            logger.info(
                "上下文构建完成 session=%s mode=%s messages=%d tokens=%d/%d "
                "history=%d (filtered=%d dropped=%d) summary=%s facts=%d degraded=%s",
                request.session_id, request.mode.value,
                len(snapshot.messages),
                snapshot.usage.total_input_tokens, snapshot.budget.context_window,
                snapshot.history_messages_used,
                snapshot.history_messages_filtered,
                snapshot.history_messages_dropped,
                snapshot.summary_included, snapshot.facts_included,
                snapshot.degraded or "[]",
            )
        return snapshot
