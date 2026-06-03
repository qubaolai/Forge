"""NullFilter: 不改变历史消息的空实现."""

from __future__ import annotations

from forge.context_mgmt.protocols import HistoryFilter
from forge.context_mgmt.types import ContextMode, HistoryMessage


class NullFilter(HistoryFilter):
    """无过滤, 原样返回."""

    @property
    def name(self) -> str:
        return "null"

    async def filter(
        self,
        messages: list[HistoryMessage],
        query: str,
        mode: ContextMode,
    ) -> list[HistoryMessage]:
        return messages
