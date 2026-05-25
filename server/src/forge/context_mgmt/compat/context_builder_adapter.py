"""老 ContextBuilder Protocol -> 新 DefaultContextBuilder 适配器.

行为 100% 等价旧 CompositeContextBuilder:
    - 用 LegacyBudgetPolicy (system 20% / dialogue 50%, 与旧 BudgetConfig 对齐)
    - 默认 RecentFilter + VerbatimPolicy
    - BuildRequest -> ContextRequest 映射
    - ContextSnapshot -> AssembledContext 反向映射

提供给 chat/orchestrator 等老调用方使用, 让外部接口零退化.
"""

from __future__ import annotations

from forge.context.base import (
    AssembledContext,
    BuildMeta,
    BuildRequest,
)
from forge.context_mgmt.builder.factory import build_context_builder
from forge.context_mgmt.filters.recent import RecentFilter
from forge.context_mgmt.meter.token_meter import DefaultTokenMeter
from forge.context_mgmt.tool_policy.verbatim import VerbatimPolicy
from forge.context_mgmt.types import (
    ContextMode,
    ContextRequest,
    ContextSnapshot,
    WindowBudget,
)
from forge.infrastructure.storage import MessageStore
from forge.llm.token_counter import TokenCounter
from forge.memory.base import MemoryStore


class LegacyBudgetPolicy:
    """与旧 BudgetConfig(system_share=0.20, history_share=0.50) 等价的比例.

    旧版没有独立的 tool_result 分区, 这里给 0 (compat 路径下 history 已包含全部).
    output 由 context_window - input 自动推出.
    """

    def allocate(self, request: ContextRequest) -> WindowBudget:
        w = request.context_window
        return WindowBudget(
            context_window=w,
            system_budget=int(w * 0.20),
            dialogue_budget=int(w * 0.50),
            tool_result_budget=0,
        )


class CompositeContextBuilderAdapter:
    """老 ContextBuilder Protocol 的实现, 内部委托 DefaultContextBuilder.

    构造函数签名与旧 CompositeContextBuilder 完全一致:
        __init__(history_repo, memory_store, token_counter)
    """

    def __init__(
        self,
        history_repo: MessageStore,
        memory_store: MemoryStore,
        token_counter: TokenCounter,
    ) -> None:
        # 显式指定 RecentFilter + VerbatimPolicy 以 100% 等价旧行为.
        # 新调用方 (走 ContextManager) 自动按 mode 选 HybridFilter + TruncatingPolicy.
        self._builder = build_context_builder(
            mode=ContextMode.CHAT,
            message_store=history_repo,
            memory_store=memory_store,
            token_meter=DefaultTokenMeter(token_counter),
            budget_policy=LegacyBudgetPolicy(),
            history_filter=RecentFilter(),
            tool_result_policy=VerbatimPolicy(),
        )

    async def build(self, request: BuildRequest) -> AssembledContext:
        ctx_request = _build_request_to_context_request(request)
        snapshot = await self._builder.build(ctx_request)
        return _snapshot_to_assembled(snapshot)


# ---------------------------------------------------------------------------
# 映射函数
# ---------------------------------------------------------------------------
def _build_request_to_context_request(req: BuildRequest) -> ContextRequest:
    """BuildRequest (旧) -> ContextRequest (新)."""
    return ContextRequest(
        user_id=req.user_id,
        session_id=req.session_id,
        current_user_message=req.current_user_message,
        mode=ContextMode.CHAT,
        # 老版本 agent.system_prompt 是已渲染好的文本 -> 用 override 跳过模板
        system_prompt_override=req.agent.system_prompt,
        context_window=req.agent.context_window,
        history_limit=req.agent.history_limit,
        enable_summary=req.agent.enable_summary,
        enable_facts=req.agent.enable_facts,
        facts_top_k=req.agent.facts_top_k,
        exclude_message_ids=req.exclude_message_ids,
        workspace_id=req.workspace_id,
        workflow_id=req.workflow_id,
        workspace_context=req.workspace_context,
        workflow_context=req.workflow_context,
        project_decisions=req.project_decisions,
        role_history=req.role_history,
        caller="chat_compat",
    )


def _snapshot_to_assembled(snapshot: ContextSnapshot) -> AssembledContext:
    """ContextSnapshot (新) -> AssembledContext (旧)."""
    meta = BuildMeta()
    meta.estimated_input_tokens = snapshot.usage.total_input_tokens
    meta.history_messages_used = snapshot.history_messages_used
    meta.history_messages_dropped = snapshot.history_messages_dropped
    meta.summary_included = snapshot.summary_included
    meta.facts_included = snapshot.facts_included
    meta.degraded = list(snapshot.degraded)
    meta.compaction_performed = snapshot.compaction_performed
    meta.compaction_token_saved = snapshot.compaction_token_saved
    meta.rebuild_count = snapshot.rebuild_count
    return AssembledContext(messages=snapshot.messages, meta=meta)
