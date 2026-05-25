"""ContextManager: 上下文管理系统的统一入口.

职责:
    1. build(request)         - 调 ContextBuilder, 按需触发压缩, 重建后返回 snapshot
    2. compact_now(session_id) - CLI / API 显式压缩入口
    3. get_current_usage(session_id) - 近实时查询上下文用量 (内存缓存)

设计:
    - 组合 ContextBuilder + CompactionController, 两者完全解耦
    - 维护 session_id -> ContextUsage 内存缓存, 用于近实时查询
    - 压缩流程: build → 检测触发 → 压缩 → 重建 → 用 tokens_saved 更新 snapshot
"""

from __future__ import annotations

import logging
from typing import Callable

from forge.context_mgmt.builder.context_builder import DefaultContextBuilder
from forge.context_mgmt.compaction.controller import CompactionController
from forge.context_mgmt.types import (
    CompactionResult,
    ContextRequest,
    ContextSnapshot,
    ContextUsage,
)

logger = logging.getLogger(__name__)


class ContextManager:
    """上下文管理系统的统一入口."""

    def __init__(
        self,
        builder: DefaultContextBuilder,
        compaction: CompactionController,
    ) -> None:
        self._builder = builder
        self._compaction = compaction
        # session_id -> 最近一次 build 后的 ContextUsage (近实时查询用)
        self._usage_cache: dict[str, ContextUsage] = {}

    async def build(self, request: ContextRequest) -> ContextSnapshot:
        """构建上下文, 按需触发压缩并重建.

        所有降级都写入 snapshot.degraded, 不抛业务异常.
        """
        snapshot = await self._builder.build(request)
        self._usage_cache[request.session_id] = snapshot.usage

        result = await self._compaction.compact_if_needed(
            request.session_id, snapshot
        )
        if result and result.success:
            # 压缩成功 → 重跑 builder, 用新 snapshot 替换
            pre_tokens = snapshot.usage.total_input_tokens
            new_snapshot = await self._builder.build(request)
            tokens_saved = max(
                0, pre_tokens - new_snapshot.usage.total_input_tokens
            )
            new_snapshot.compaction_performed = True
            new_snapshot.compaction_token_saved = tokens_saved
            new_snapshot.rebuild_count = snapshot.rebuild_count + 1
            new_snapshot.degraded.append("active_compaction_triggered")
            self._usage_cache[request.session_id] = new_snapshot.usage
            logger.info(
                "压缩重建完成 session=%s tokens %d -> %d (节省 %d)",
                request.session_id, pre_tokens,
                new_snapshot.usage.total_input_tokens, tokens_saved,
            )
            return new_snapshot
        elif result and not result.success:
            snapshot.degraded.append("compaction_failed")

        return snapshot

    async def compact_now(
        self,
        session_id: str,
        snapshot: ContextSnapshot | None = None,
    ) -> CompactionResult:
        """CLI / API 显式压缩入口.

        snapshot 可选: 若不传, 调用方默认不需要后续重建 (仅触发 summary 写入).
        若调用方需要紧接着重建 messages, 应先调 build() 拿 snapshot 再 compact_now().
        """
        return await self._compaction.compact_now(session_id, snapshot)

    def get_current_usage(self, session_id: str) -> ContextUsage | None:
        """近实时查询: 返回该 session 最近一次构建的用量快照.

        无 IO, 纯内存读取, 延迟 < 1ms.
        CLI 和前端可按需 poll (建议间隔 1-2s) 或在 SSE 事件后拉取.
        """
        return self._usage_cache.get(session_id)

    def invalidate_usage(self, session_id: str) -> None:
        """清除某 session 的用量缓存 (会话删除时调用)."""
        self._usage_cache.pop(session_id, None)


# ---------------------------------------------------------------------------
# 工厂: 按 mode 装配默认 ContextManager.
# ---------------------------------------------------------------------------
_DEFAULT_MANAGER_FACTORY: Callable[..., ContextManager] | None = None


def build_context_manager(
    *,
    mode,
    message_store,
    memory_store=None,
    compaction_strategy=None,
    compaction_trigger=None,
    history_filter=None,
    tool_result_policy=None,
    budget_policy=None,
    token_meter=None,
    extra_providers=None,
) -> ContextManager:
    """组装一个 ContextManager.

    按 mode 选默认压缩策略:
        - CHAT:     SummaryCompaction + ThresholdTrigger(0.85)
        - TASK:     NullCompaction
        - WORKFLOW: NullCompaction
    """
    from forge.context_mgmt.builder.factory import build_context_builder
    from forge.context_mgmt.compaction.strategies.null import NullCompaction
    from forge.context_mgmt.compaction.strategies.summary import SummaryCompaction
    from forge.context_mgmt.compaction.trigger.threshold import (
        DEFAULT_THRESHOLD,
        ThresholdTrigger,
    )
    from forge.context_mgmt.types import ContextMode

    if memory_store is None:
        from forge.context.factory import get_memory_store
        memory_store = get_memory_store()

    builder = build_context_builder(
        mode=mode,
        message_store=message_store,
        memory_store=memory_store,
        history_filter=history_filter,
        tool_result_policy=tool_result_policy,
        budget_policy=budget_policy,
        token_meter=token_meter,
        extra_providers=extra_providers,
    )

    if compaction_strategy is None:
        if mode == ContextMode.CHAT:
            compaction_strategy = SummaryCompaction()
        else:
            compaction_strategy = NullCompaction()
    if compaction_trigger is None:
        compaction_trigger = ThresholdTrigger(DEFAULT_THRESHOLD)

    controller = CompactionController(compaction_strategy, compaction_trigger)
    return ContextManager(builder, controller)
