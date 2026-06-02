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
from typing import Any

from forge.context_mgmt.digest.policy import DigestPolicy
from forge.context_mgmt.digest.types import DigestLookup
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
        digest_policy: DigestPolicy | None = None,
        digest_cap: int = 0,
        digest_store: Any = None,
    ) -> None:
        self._store = message_store
        self._filter = history_filter
        self._tool_policy = tool_result_policy
        self._meter = token_meter
        # digest 引用化 (止血): 单条超 cap 的消息折叠为引用占位。
        # digest_policy=None 或 digest_cap<=0 时完全旁路, 行为与改动前一致。
        self._digest_policy = digest_policy
        self._digest_cap = digest_cap
        # digest 缓存读源 (DigestStore, 需含 async batch_get(ids) -> Mapping)。
        # 为 None 时 DigestPolicy 全部走廉价截断降级 (= 阶段 1 行为)。
        self._digest_store = digest_store

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

        # 单条 cap 闸: 把超长消息引用化折叠 (filter 之后、构造 Message 之前)。
        # 与 MessageAssembler 的累计预算闸形成「单条 cap + 累计 budget」双闸。
        digest_flags: list[str] = []
        digest_info: list[str] = []
        if self._digest_policy is not None and self._digest_cap > 0:
            lookup: DigestLookup | None = await self._build_digest_lookup(filtered)
            result = self._digest_policy.apply(
                filtered, cap=self._digest_cap, meter=self._meter, lookup=lookup
            )
            filtered = result.messages
            digest_flags = result.degraded_flags
            digest_info = result.info_flags
            if result.substituted or result.pending:
                # 命中率可观测: 无损命中 vs 降级 (缓存未命中) 计数
                logger.info(
                    "digest 折叠 session=%s 命中=%d 降级=%d",
                    request.session_id, result.substituted, result.pending,
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
            degraded=digest_flags,
            info=digest_info,
        )
        return [chunk]

    async def _build_digest_lookup(
        self, messages: list[HistoryMessage]
    ) -> DigestLookup | None:
        """批量预取已缓存 digest (一次 IN 查询, 避免逐条查库)。

        无 digest_store / 查询失败时返回 None -> DigestPolicy 走廉价截断降级,
        由异步 content.digest 任务下一轮补上缓存。
        """
        if self._digest_store is None or not messages:
            return None
        try:
            return await self._digest_store.batch_get([hm.id for hm in messages])
        except Exception as exc:  # noqa: BLE001 — 缓存读失败软降级
            logger.warning("digest 缓存预取失败, 走截断降级: %s", exc)
            return None

    # aborted / error 消息不应带入 LLM 上下文
    _SKIP_STATUSES = frozenset({"aborted", "error", "streaming"})

    @staticmethod
    def _rows_to_history_messages(
        rows, exclude: set[str]
    ) -> list[HistoryMessage]:
        """ChatMessageView -> HistoryMessage 列表.

        仅保留 role in (user, assistant)、content 非空、且 status 为正常终态。
        """
        skip = HistoryProvider._SKIP_STATUSES
        out: list[HistoryMessage] = []
        for idx, r in enumerate(rows):
            if r.id in exclude:
                continue
            if r.role not in ("user", "assistant"):
                continue
            if not r.content:
                continue
            if getattr(r, "status", None) in skip:
                continue
            out.append(
                HistoryMessage(
                    message=Message(role=r.role, content=r.content),
                    id=r.id,
                    turn_index=idx,
                )
            )
        return out
