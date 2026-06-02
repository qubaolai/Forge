"""StepScopedFilter: workflow 模式专用.

只保留 turn_index 与当前 step_id 匹配的消息, 实现步骤间上下文隔离.
当前实现假设 HistoryMessage 的 turn_index 与 step 编号对应,
后续 workflow 模式真实接入时, HistoryProvider 需要把 step_id 写入 HistoryMessage.
"""

from __future__ import annotations

from forge.context_mgmt.protocols import HistoryFilter
from forge.context_mgmt.types import ContextMode, HistoryMessage


class StepScopedFilter(HistoryFilter):
    """按 step 隔离历史消息 (workflow 模式默认).

    阶段 4 当前实现: 占位 (返回空列表, 即 workflow 步骤无对话历史).
    后续: HistoryMessage 增加 step_id 字段后, 改为按 step_id 过滤.
    """

    @property
    def name(self) -> str:
        return "step_scoped"

    async def filter(
        self,
        messages: list[HistoryMessage],
        query: str,
        mode: ContextMode,
    ) -> list[HistoryMessage]:
        return []
