"""RecentFilter: 按时间顺序返回历史 (当前行为, 兜底实现).

不做相关性判断, 仅按 history_limit 截断后原样返回.
chat 模式默认会用 HybridFilter, 这里是降级路径.
"""

from __future__ import annotations

from forge.context_mgmt.types import ContextMode, HistoryMessage


class RecentFilter:
    """无过滤, 原样返回. 等价于现有 CompositeContextBuilder 的行为."""

    @property
    def name(self) -> str:
        return "recent"

    async def filter(
        self,
        messages: list[HistoryMessage],
        query: str,
        mode: ContextMode,
    ) -> list[HistoryMessage]:
        return messages
