"""DefaultContextBuilder 装配工厂.

build_context_builder(mode, message_store, ...) 根据 ContextMode 选择默认实现:

阶段 1 行为 (等价 CompositeContextBuilder):
    - HistoryFilter:    RecentFilter (不过滤)
    - ToolResultPolicy: VerbatimPolicy (保持原样)

阶段 2 引入 ToolResultPolicy 后, 各 mode 切换到对应默认 (TruncatingPolicy/EvictingPolicy 等).
阶段 4 引入 HybridFilter 后, CHAT 模式切换到 HybridFilter.
"""

from __future__ import annotations

from forge.context_mgmt.budget.policy import DefaultBudgetPolicy
from forge.context_mgmt.builder.content_gatherer import ContentGatherer
from forge.context_mgmt.builder.context_builder import DefaultContextBuilder
from forge.context_mgmt.builder.message_assembler import MessageAssembler
from forge.context_mgmt.builder.prompt_renderer import PromptRenderer
from forge.context_mgmt.filters.hybrid import HybridFilter
from forge.context_mgmt.filters.null import NullFilter
from forge.context_mgmt.filters.recent import RecentFilter
from forge.context_mgmt.filters.step_scoped import StepScopedFilter
from forge.context_mgmt.meter.token_meter import get_token_meter
from forge.context_mgmt.protocols import (
    BudgetPolicy,
    ContentProvider,
    HistoryFilter,
    TokenMeter,
    ToolResultPolicy,
)
from forge.context_mgmt.providers.facts import FactsProvider
from forge.context_mgmt.providers.history import HistoryProvider
from forge.context_mgmt.providers.summary import SummaryProvider
from forge.context_mgmt.providers.workspace import WorkspaceProvider
from forge.context_mgmt.tool_policy.verbatim import VerbatimPolicy
from forge.context_mgmt.types import ContextMode
from forge.infrastructure.storage import MessageStore
from forge.memory.base import MemoryStore


def _default_history_filter(mode: ContextMode) -> HistoryFilter:
    """按 mode 选默认 HistoryFilter.

    CHAT:     HybridFilter (近期锚点 + 语义过滤; 当前 SemanticFilter NullScorer 兜底,
              等价 RecentFilter 行为, 接入 embedder 后即时生效)
    TASK:     NullFilter (不要历史)
    WORKFLOW: StepScopedFilter (按 step 隔离)
    其他:     RecentFilter (兜底)
    """
    if mode == ContextMode.TASK:
        return NullFilter()
    if mode == ContextMode.WORKFLOW:
        return StepScopedFilter()
    if mode == ContextMode.CHAT:
        return HybridFilter()
    return RecentFilter()


def _default_tool_result_policy(mode: ContextMode) -> ToolResultPolicy:
    """按 mode 选默认 ToolResultPolicy.

    CHAT:     TruncatingPolicy (跨轮加载的大 tool 结果截断到 500 token)
    TASK:     EvictingPolicy (跨轮 tool 结果用占位符替代; 当轮执行不受影响)
    WORKFLOW: SummarizingPolicy (LLM 摘要; 当前降级到 Truncating)
    其他:     VerbatimPolicy
    """
    from forge.context_mgmt.tool_policy.evicting import EvictingPolicy
    from forge.context_mgmt.tool_policy.summarizing import SummarizingPolicy
    from forge.context_mgmt.tool_policy.truncating import TruncatingPolicy

    if mode == ContextMode.CHAT:
        return TruncatingPolicy()
    if mode == ContextMode.TASK:
        return EvictingPolicy()
    if mode == ContextMode.WORKFLOW:
        return SummarizingPolicy()
    return VerbatimPolicy()


def build_context_builder(
    *,
    mode: ContextMode,
    message_store: MessageStore,
    memory_store: MemoryStore,
    history_filter: HistoryFilter | None = None,
    tool_result_policy: ToolResultPolicy | None = None,
    budget_policy: BudgetPolicy | None = None,
    token_meter: TokenMeter | None = None,
    extra_providers: list[ContentProvider] | None = None,
) -> DefaultContextBuilder:
    """组装一个 DefaultContextBuilder.

    Args:
        mode:              业务模式, 决定默认 Filter / Policy.
        message_store:     当前请求持有的 MessageStore (含 DB session).
        memory_store:      长寿单例, 由调用方传入 (避免本工厂耦合 memory.factory).
        history_filter:    可选, 默认按 mode 选择.
        tool_result_policy: 可选, 默认按 mode 选择.
        budget_policy:     可选, 默认 DefaultBudgetPolicy.
        token_meter:       可选, 默认全局单例.
        extra_providers:   附加 Provider (如 workflow_step), 追加到默认 Provider 列表.
    """
    meter = token_meter or get_token_meter()
    h_filter = history_filter or _default_history_filter(mode)
    t_policy = tool_result_policy or _default_tool_result_policy(mode)
    b_policy = budget_policy or DefaultBudgetPolicy()

    providers: list[ContentProvider] = [
        HistoryProvider(message_store, h_filter, t_policy, meter),
        SummaryProvider(memory_store, meter),
        FactsProvider(memory_store, meter),
        WorkspaceProvider(meter),
    ]
    if extra_providers:
        providers.extend(extra_providers)

    return DefaultContextBuilder(
        renderer=PromptRenderer(),
        gatherer=ContentGatherer(providers),
        assembler=MessageAssembler(meter),
        budget_policy=b_policy,
    )
