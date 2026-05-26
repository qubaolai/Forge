"""NullFilter: 返回空列表 (task 模式默认, 完全不要历史)."""

from __future__ import annotations

from forge.context_mgmt.types import ContextMode, HistoryMessage
from forge.context_mgmt.protocols import HistoryFilter


class NullFilter(HistoryFilter):
    @property
    def name(self) -> str:
        return "null"

    async def filter(
        self,
        messages: list[HistoryMessage],
        query: str,
        mode: ContextMode,
    ) -> list[HistoryMessage]:
        return []
