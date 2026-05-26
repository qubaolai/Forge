"""FactsProvider: 召回用户长期事实.

等价于 CompositeContextBuilder._safe_recall_facts().
"""

from __future__ import annotations

import logging

from forge.context_mgmt.protocols import ContentProviderError, TokenMeter
from forge.context_mgmt.types import ContentChunk, ContextRequest
from forge.memory.base import FactRecallRequest, MemoryStore, MemoryStoreError
from forge.context_mgmt.protocols import ContentProvider

logger = logging.getLogger(__name__)


class FactsProvider(ContentProvider):
    """长期事实提供者."""

    def __init__(self, memory_store: MemoryStore, token_meter: TokenMeter) -> None:
        self._memory = memory_store
        self._meter = token_meter

    @property
    def name(self) -> str:
        return "facts"

    async def provide(self, request: ContextRequest) -> list[ContentChunk]:
        if not request.enable_facts:
            return []
        try:
            facts = await self._memory.recall_facts(
                FactRecallRequest(
                    user_id=request.user_id,
                    query=request.current_user_message,
                    top_k=request.facts_top_k,
                )
            )
        except MemoryStoreError as exc:
            logger.warning("召回长期事实失败: %s", exc)
            raise ContentProviderError("facts_recall_failed", str(exc)) from exc

        if not facts:
            return []

        lines = ["## 关于用户 (长期记忆)"]
        lines.extend(f"- {f.content}" for f in facts)
        text = "\n".join(lines)
        return [
            ContentChunk(
                kind="facts",
                layer="facts",
                text=text,
                estimated_tokens=self._meter.count_text(text),
                message_count=len(facts),
            )
        ]
