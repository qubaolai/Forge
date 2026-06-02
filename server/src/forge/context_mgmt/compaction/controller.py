"""CompactionController: 压缩子系统对外门面.

与 ContextBuilder 完全解耦, 可由三种方式触发:
    1. ContextManager 在每次 build 后自动检查 (compact_if_needed)
    2. CLI 命令显式触发 (compact_now)
    3. API 端点触发 (compact_now)

设计原则:
    - 触发判断 (CompactionTrigger) 和执行 (CompactionStrategy) 完全解耦
    - 失败统一捕获: CompactionError 转 CompactionResult(success=False), 不向上抛
"""

from __future__ import annotations

import logging

from forge.context_mgmt.protocols import (
    CompactionError,
    CompactionStrategy,
    CompactionTrigger,
)
from forge.context_mgmt.types import CompactionResult, ContextSnapshot

logger = logging.getLogger(__name__)


class CompactionController:
    """压缩控制器, 组合 Trigger + Strategy."""

    def __init__(
        self,
        strategy: CompactionStrategy,
        trigger: CompactionTrigger,
    ) -> None:
        self._strategy = strategy
        self._trigger = trigger

    @property
    def strategy_name(self) -> str:
        return self._strategy.name

    @property
    def trigger_name(self) -> str:
        return self._trigger.name

    async def compact_if_needed(
        self,
        session_id: str,
        snapshot: ContextSnapshot,
    ) -> CompactionResult | None:
        """由 ContextManager 在每次 build 后自动调用.

        Returns:
            None: 不满足触发条件, 未压缩.
            CompactionResult(success=True): 压缩成功 (调用方应重建上下文).
            CompactionResult(success=False): 压缩失败 (调用方降级).
        """
        if not self._trigger.should_compact(snapshot):
            return None
        return await self._safe_compact(
            session_id, snapshot, trigger_source="threshold"
        )

    async def compact_now(
        self,
        session_id: str,
        snapshot: ContextSnapshot | None = None,
    ) -> CompactionResult:
        """CLI / API 显式触发, 绕过 trigger 直接执行.

        Args:
            snapshot: 可选 (无则用 ExplicitTrigger 空 snapshot, 表示无前置统计).
        """
        if snapshot is None:
            # 显式触发不需要 snapshot, 但 SummaryCompaction 可能用 snapshot 计算 tokens_saved
            from forge.context_mgmt.types import ContextUsage, WindowBudget
            snapshot = ContextSnapshot(
                messages=[],
                budget=WindowBudget(
                    context_window=0,
                    system_budget=0,
                    dialogue_budget=0,
                    tool_result_budget=0,
                ),
                usage=ContextUsage(
                    context_window=0, total_input_tokens=0,
                    max_output_tokens=0, total_ratio=0.0,
                ),
            )
        return await self._safe_compact(
            session_id, snapshot, trigger_source="explicit"
        )

    async def _safe_compact(
        self,
        session_id: str,
        snapshot: ContextSnapshot,
        *,
        trigger_source: str,
    ) -> CompactionResult:
        """统一捕获 CompactionError, 转换为 CompactionResult(success=False)."""
        try:
            result = await self._strategy.compact(session_id, snapshot)
            result.trigger_source = trigger_source
            logger.info(
                "压缩完成 session=%s strategy=%s trigger=%s success=%s",
                session_id, self._strategy.name, trigger_source, result.success,
            )
            return result
        except CompactionError as exc:
            logger.warning(
                "压缩失败 session=%s strategy=%s trigger=%s: %s",
                session_id, self._strategy.name, trigger_source, exc,
            )
            return CompactionResult(
                success=False,
                strategy_used=self._strategy.name,
                trigger_source=trigger_source,
                failure_reason=str(exc),
            )
