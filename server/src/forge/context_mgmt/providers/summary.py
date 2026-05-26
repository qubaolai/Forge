"""SummaryProvider: 从 MemoryStore 读取 session 摘要.

等价于 CompositeContextBuilder._safe_get_summary().
"""

from __future__ import annotations

import logging

from forge.context_mgmt.protocols import ContentProviderError, TokenMeter, ContentProvider
from forge.context_mgmt.types import ContentChunk, ContextRequest
from forge.memory.base import MemoryStore, MemoryStoreError

logger = logging.getLogger(__name__)


class SummaryProvider(ContentProvider):
    """摘要提供者."""

    def __init__(self, memory_store: MemoryStore, token_meter: TokenMeter) -> None:
        self._memory = memory_store
        self._meter = token_meter

    @property
    def name(self) -> str:
        return "summary"

    async def provide(self, request: ContextRequest) -> list[ContentChunk]:
        if not request.enable_summary:
            return []
        try:
            summary = await self._memory.get_summary(
                request.session_id, workspace_id=request.workspace_id
            )
        except MemoryStoreError as exc:
            logger.warning("取摘要失败: %s", exc)
            raise ContentProviderError("summary_fetch_failed", str(exc)) from exc

        if summary is None or not summary.content:
            return []

        text = "## 早期对话摘要\n" + summary.content.strip()
        return [
            ContentChunk(
                kind="summary",
                layer="summary",
                text=text,
                estimated_tokens=self._meter.count_text(text),
                message_count=0,
            )
        ]
