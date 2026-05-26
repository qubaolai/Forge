"""HistoryProvider: 从 MessageStore 加载历史并应用 Filter + ToolResultPolicy.

阶段 1 行为 (等价 CompositeContextBuilder._orm_to_messages):
    - 只保留 role in (user, assistant) 且 content 非空的消息
    - 过滤掉 exclude_message_ids
    - 不分 dialogue / tool_results, 全部归入 layer="dialogue"
    - 应用 HistoryFilter (默认 RecentFilter, 无过滤)

阶段 2 扩展:
    - 拆分 tool_results 单独成 chunk
    - 应用 ToolResultPolicy 处理大结果
"""

from __future__ import annotations

import logging

from forge.context_mgmt.protocols import (
    HistoryFilter,
    TokenMeter,
    ToolResultPolicy,
    ContentProvider
)
from forge.context_mgmt.types import (
    ContentChunk,
    ContextRequest,
    HistoryMessage,
)
from forge.core.types.message import Message
from forge.infrastructure.storage import MessageStore

logger = logging.getLogger(__name__)


class HistoryProvider(ContentProvider):
    """历史消息提供者.

    Note:
        MessageStore 持有 DB session, 必须按请求 new (在 build_context_manager 工厂中传入).
    """

    def __init__(
        self,
        message_store: MessageStore,
        history_filter: HistoryFilter,
        tool_result_policy: ToolResultPolicy,
        token_meter: TokenMeter,
    ) -> None:
        self._store = message_store
        self._filter = history_filter
        self._tool_policy = tool_result_policy
        self._meter = token_meter

    @property
    def name(self) -> str:
        return "history"

    async def provide(self, request: ContextRequest) -> list[ContentChunk]:
        if request.history_limit <= 0:
            return []
        # history 是核心上下文, 失败直接向上传播 (不包装为 ContentProviderError).
        # 与旧 CompositeContextBuilder 行为一致: 没历史不能假装继续.
        rows = await self._store.load_recent(
            request.session_id, limit=request.history_limit
        )

        exclude = set(request.exclude_message_ids)
        history_messages = self._rows_to_history_messages(rows, exclude)
        candidate_count = len(history_messages)

        # 应用 HistoryFilter (阶段 1: RecentFilter 不过滤)
        filtered = await self._filter.filter(
            history_messages, request.current_user_message, request.mode
        )

        # 转换为 Message 列表 (按 turn_index 顺序)
        out_messages: list[Message] = [hm.message for hm in filtered]
        estimated = self._meter.count_messages(out_messages)

        chunk = ContentChunk(
            kind="history",
            layer="dialogue",
            messages=out_messages,
            estimated_tokens=estimated,
            message_count=len(out_messages),
            truncated=(len(filtered) < candidate_count),
        )
        return [chunk]

    @staticmethod
    def _rows_to_history_messages(
        rows, exclude: set[str]
    ) -> list[HistoryMessage]:
        """ChatMessageView -> HistoryMessage 列表.

        仅保留 role in (user, assistant) 且 content 非空,
        与现有 CompositeContextBuilder._orm_to_messages 等价.
        """
        out: list[HistoryMessage] = []
        for idx, r in enumerate(rows):
            if r.id in exclude:
                continue
            if r.role not in ("user", "assistant"):
                continue
            if not r.content:
                continue
            out.append(
                HistoryMessage(
                    message=Message(role=r.role, content=r.content),
                    id=r.id,
                    turn_index=idx,
                )
            )
        return out
