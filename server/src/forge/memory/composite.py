"""组合 MemoryStore: 把 SummaryStore + (将来) FactStore 拼成完整 MemoryStore.

实现 MemoryStore Protocol:
    get_summary    -> SummaryStore.get
    recall_facts   -> Stage 2 永远返回 []; Stage 3 接入 FactStore

读路径失败 -> 抛 MemoryStoreError, ContextBuilder 上游会捕获 + 降级.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge.memory.base import Fact, FactRecallRequest, Summary

if TYPE_CHECKING:
    from forge.infrastructure.storage import SummaryStore


class CompositeMemoryStore:
    """Stage 2: 只接 SummaryStore. PR #5 加 FactStore 参数."""

    def __init__(self, summary_store: SummaryStore) -> None:
        self._summary_store = summary_store

    async def get_summary(
        self,
        session_id: str,
        *,
        workspace_id: str | None = None,
    ) -> Summary | None:
        return await self._summary_store.get(session_id, workspace_id=workspace_id)

    async def recall_facts(self, request: FactRecallRequest) -> list[Fact]:
        # PR #5 接 FactStore 后改为 self._fact_store.recall(request)
        return []
