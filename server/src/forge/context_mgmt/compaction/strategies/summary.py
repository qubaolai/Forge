"""SummaryCompaction: 调 SummaryService 生成早期对话摘要, 替换历史尾部.

从 forge.chat.assembler.ContextAssembler.compact_and_reassemble() 移入.

行为:
    1. 调 SummaryService.summarize_session() 生成最新摘要 (写入 SummaryStore)
    2. 上层 (ContextManager) 在压缩成功后重跑 ContextBuilder, 这样
       SummaryProvider 自动拉到新摘要, dialogue 段会变短.

失败:
    - InfrastructureError -> raise CompactionError, ContextManager 捕获降级.
    - 其他异常 -> 同样包装为 CompactionError.
"""

from __future__ import annotations

import logging

from forge.context_mgmt.protocols import CompactionError
from forge.context_mgmt.types import CompactionResult, ContextSnapshot

logger = logging.getLogger(__name__)


class SummaryCompaction:
    """基于 SummaryService 的摘要压缩."""

    def __init__(self, summary_service=None) -> None:
        """summary_service: 可注入, 默认走 get_summary_service() 单例."""
        self._service = summary_service

    @property
    def name(self) -> str:
        return "summary"

    def _get_service(self):
        if self._service is not None:
            return self._service
        from forge.memory.summary.service import get_summary_service
        return get_summary_service()

    async def compact(
        self,
        session_id: str,
        snapshot: ContextSnapshot,
    ) -> CompactionResult:
        from forge.memory.summary.service import InfrastructureError

        pre_tokens = snapshot.usage.total_input_tokens
        try:
            await self._get_service().summarize_session(session_id, workspace_id=None)
        except InfrastructureError as exc:
            logger.warning(
                "SummaryCompaction 失败 session=%s: %s -- 上层应降级",
                session_id, exc,
            )
            raise CompactionError(f"summarize_session failed: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "SummaryCompaction 崩溃 session=%s -- 上层应降级", session_id,
            )
            raise CompactionError(f"summarize_session unexpected: {exc}") from exc

        # 注意: tokens_saved 由 ContextManager 在重跑 builder 后计算并填入,
        # 因为这里还没有重建上下文, 不知道节省了多少.
        return CompactionResult(
            success=True,
            tokens_saved=0,
            strategy_used="summary",
        )
