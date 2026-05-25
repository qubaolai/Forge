"""NullCompaction: 不做任何压缩 (task / workflow 模式默认)."""

from __future__ import annotations

from forge.context_mgmt.types import CompactionResult, ContextSnapshot


class NullCompaction:
    """什么都不做, 始终返回 success=False."""

    @property
    def name(self) -> str:
        return "null"

    async def compact(
        self,
        session_id: str,
        snapshot: ContextSnapshot,
    ) -> CompactionResult:
        return CompactionResult(
            success=False,
            strategy_used="null",
            failure_reason="NullCompaction does nothing",
        )
