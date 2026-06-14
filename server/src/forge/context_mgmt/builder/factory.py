"""DefaultContextBuilder 装配工厂.

build_context_builder(message_store, ...) 组装 chat 路径的默认实现:
    HybridFilter (近期锚点 + 可选语义过滤) + TruncatingPolicy + DefaultBudgetPolicy。
"""

from __future__ import annotations

import logging
from typing import Any

from forge.context_mgmt.budget.policy import DefaultBudgetPolicy
from forge.context_mgmt.builder.content_gatherer import ContentGatherer
from forge.context_mgmt.builder.context_builder import DefaultContextBuilder
from forge.context_mgmt.builder.message_assembler import MessageAssembler
from forge.context_mgmt.builder.prompt_renderer import PromptRenderer
from forge.context_mgmt.digest.policy import DigestPolicy
from forge.context_mgmt.filters.hybrid import EmbeddingScorer, HybridFilter
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
from forge.infrastructure.storage import MessageStore
from forge.memory.base import MemoryStore

logger = logging.getLogger(__name__)


def _default_history_filter() -> HistoryFilter:
    """默认 HistoryFilter: HybridFilter (近期锚点 + 语义过滤)。

    settings.context.semantic_recall 开启且 embedder 可用时, 用 EmbeddingScorer
    (读缓存向量) 给早期轮次打分; 否则不做语义过滤。
    关闭 / 装配失败时回退默认 HybridFilter, 不做语义过滤。
    """
    try:
        from forge.config.settings import get_settings
        cfg = get_settings().context.semantic_recall
    except Exception:  # noqa: BLE001 — 无配置环境 (单测) 走默认
        return HybridFilter()

    if not cfg.enabled:
        return HybridFilter(anchor_turns=cfg.anchor_turns if cfg.enabled else 3)

    try:
        from forge.context_mgmt.recall.embedding_store import MessageEmbeddingStore
        from forge.infrastructure.database.database import get_session_factory
        from forge.retrieval.bound_model_resolver import get_bound_model_resolver

        store = MessageEmbeddingStore(get_session_factory())
        scorer = EmbeddingScorer(
            None,
            store,
            resolver=lambda: get_bound_model_resolver().resolve("semantic_history_embedding"),
        )
        return HybridFilter(
            scorer=scorer,
            min_score=cfg.min_score,
            anchor_turns=cfg.anchor_turns,
        )
    except Exception as exc:  # noqa: BLE001 — DB 未就绪时整体降级
        logger.warning("语义召回不可用, 降级为近期锚点保留: %s", exc)
        return HybridFilter()


def _default_tool_result_policy() -> ToolResultPolicy:
    """默认 ToolResultPolicy: TruncatingPolicy (跨轮加载的大 tool 结果截断到 500 token)."""
    from forge.context_mgmt.tool_policy.truncating import TruncatingPolicy

    return TruncatingPolicy()


def _default_digest() -> tuple[DigestPolicy | None, int, Any]:
    """按 settings.context.digest 构造默认 DigestPolicy + 单条 cap + DigestStore.

    关闭 / 未配置 / cap<=0 时返回 (None, 0, None), HistoryProvider 完全旁路 digest。
    读 settings 失败 (如单测无配置) 同样安全降级为旁路。
    DigestStore 构造依赖 DB engine 就绪, 失败时置 None (digest 仍走廉价截断降级)。
    """
    try:
        from forge.config.settings import get_settings
        cfg = get_settings().context.digest
    except Exception:  # noqa: BLE001 — 无配置环境 (单测) 安全旁路
        return None, 0, None
    if not cfg.enabled or cfg.per_message_token_cap <= 0:
        return None, 0, None
    store: Any = None
    try:
        from forge.context_mgmt.digest.store import DigestStore
        from forge.infrastructure.database.database import get_session_factory
        store = DigestStore(get_session_factory())
    except Exception:  # noqa: BLE001 — DB 未就绪时降级为无缓存
        store = None
    return DigestPolicy(), cfg.per_message_token_cap, store


def build_context_builder(
    *,
    message_store: MessageStore,
    memory_store: MemoryStore,
    history_filter: HistoryFilter | None = None,
    tool_result_policy: ToolResultPolicy | None = None,
    budget_policy: BudgetPolicy | None = None,
    token_meter: TokenMeter | None = None,
    extra_providers: list[ContentProvider] | None = None,
    digest_policy: DigestPolicy | None = None,
    digest_cap: int | None = None,
    digest_store: Any = None,
) -> DefaultContextBuilder:
    """组装一个 DefaultContextBuilder.

    Args:
        message_store:     当前请求持有的 MessageStore (含 DB session).
        memory_store:      长寿单例, 由调用方传入 (避免本工厂耦合 memory.factory).
        history_filter:    可选, 默认 HybridFilter.
        tool_result_policy: 可选, 默认 TruncatingPolicy.
        budget_policy:     可选, 默认 DefaultBudgetPolicy.
        token_meter:       可选, 默认全局单例.
        extra_providers:   附加 Provider, 追加到默认 Provider 列表.
        digest_policy:     可选, 单条超长消息引用化策略; 默认按 settings.context.digest 构造.
        digest_cap:        可选, 单条折叠 token 上限; 默认取自 settings。
    """
    meter = token_meter or get_token_meter()
    h_filter = history_filter or _default_history_filter()
    t_policy = tool_result_policy or _default_tool_result_policy()
    b_policy = budget_policy or DefaultBudgetPolicy()

    # 显式传入则用之 (单测 / 特殊编排); 否则按 settings 取默认。
    if digest_policy is None and digest_cap is None:
        d_policy, d_cap, d_store = _default_digest()
        d_store = digest_store or d_store
    else:
        d_policy, d_cap, d_store = digest_policy, (digest_cap or 0), digest_store

    providers: list[ContentProvider] = [
        HistoryProvider(
            message_store, h_filter, t_policy, meter,
            digest_policy=d_policy, digest_cap=d_cap, digest_store=d_store,
        ),
        SummaryProvider(memory_store, meter),
        FactsProvider(memory_store, meter),
    ]
    if extra_providers:
        providers.extend(extra_providers)

    return DefaultContextBuilder(
        renderer=PromptRenderer(),
        gatherer=ContentGatherer(providers),
        assembler=MessageAssembler(meter),
        budget_policy=b_policy,
    )
