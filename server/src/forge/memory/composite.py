"""组合 MemoryStore: 把 SummaryStore + FactStore 拼成完整 MemoryStore.

实现 MemoryStore ABC:
    get_summary    -> SummaryStore.get
    recall_facts   -> FactStore.recall (未装配 FactStore 时返回 [])

读路径失败 -> 抛 MemoryStoreError, ContextBuilder 上游会捕获 + 降级.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge.memory.base import Fact, FactRecallRequest, MemoryStore, Summary

if TYPE_CHECKING:
    from forge.infrastructure.storage import FactStore, SummaryStore


class CompositeMemoryStore(MemoryStore):
    """SummaryStore 必接; FactStore 可选 (facts.enabled=false 时为 None)."""

    def __init__(
        self,
        summary_store: SummaryStore,
        fact_store: FactStore | None = None,
    ) -> None:
        self._summary_store = summary_store
        self._fact_store = fact_store

    async def get_summary(self, session_id: str) -> Summary | None:
        return await self._summary_store.get(session_id)

    async def recall_facts(self, request: FactRecallRequest) -> list[Fact]:
        if self._fact_store is None:
            return []
        return await self._fact_store.recall(request)
