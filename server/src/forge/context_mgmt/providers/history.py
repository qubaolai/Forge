"""HistoryProvider: 从 MessageStore 加载历史并应用 Filter + ToolResultPolicy.

阶段 1 行为 (等价 CompositeContextBuilder._orm_to_messages):
    - 只保留 role in (user, assistant) 且 content 非空的消息
    - 过滤掉 exclude_message_ids
    - 不分 dialogue / tool_results, 全部归入 layer="dialogue"
    - 应用 HistoryFilter

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
    ContentProvider,
    HistoryFilter,
    TokenMeter,
    ToolResultPolicy,
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
        MessageStore 持有 DB session, 必须按请求 new (在 build_context_builder 工厂中传入).
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
        candidate_ids = [hm.id for hm in history_messages]

        # 应用 HistoryFilter
        filtered = await self._filter.filter(
            history_messages, request.current_user_message
        )
        filtered_ids = {hm.id for hm in filtered}
        filtered_out_ids = [mid for mid in candidate_ids if mid not in filtered_ids]

        # 单条 cap 闸: 把超长消息引用化折叠 (filter 之后、构造 Message 之前)。
        # 与 MessageAssembler 的累计预算闸形成「单条 cap + 累计 budget」双闸。
        digest_flags: list[str] = []
        digest_info: list[str] = []
        digest_pending_ids: list[str] = []
        digest_substituted_ids: list[str] = []
        costs: list[int] | None = None
        if self._digest_policy is not None and self._digest_cap > 0:
            lookup: DigestLookup | None = await self._build_digest_lookup(
                filtered, self._digest_cap
            )
            result = self._digest_policy.apply(
                filtered, cap=self._digest_cap, meter=self._meter, lookup=lookup
            )
            filtered = result.messages
            digest_flags = result.degraded_flags
            digest_info = result.info_flags
            digest_pending_ids = list(result.pending_ids)
            digest_substituted_ids = list(result.substituted_ids)
            costs = result.message_tokens  # 折叠后每条 token 数 (供 assembler 免重复算)
            if result.substituted or result.pending:
                # 命中率可观测: 无损命中 vs 降级 (缓存未命中) 计数
                logger.info(
                    "digest 折叠 session=%s 命中=%d 降级=%d",
                    request.session_id, result.substituted, result.pending,
                )

        # 转换为 Message 列表 (按 turn_index 顺序)
        out_messages: list[Message] = [hm.message for hm in filtered]
        # 每条 token 数: 优先用 digest 折叠时算出的 costs (未折叠用落库携带值),
        # digest 旁路时用携带值/实时算 —— 避免在热路径对历史重复 tiktoken。
        if costs is None:
            costs = [self._msg_cost(hm) for hm in filtered]
        estimated = sum(costs) if costs else 0

        chunk = ContentChunk(
            kind="history",
            layer="dialogue",
            messages=out_messages,
            estimated_tokens=estimated,
            message_count=len(out_messages),
            truncated=(len(filtered) < candidate_count),
            degraded=digest_flags,
            info=digest_info,
            message_tokens=costs,
            details={
                "candidate_ids": candidate_ids,
                "after_filter_ids": [hm.id for hm in filtered],
                "filtered_ids": filtered_out_ids,
                "digest_pending_ids": digest_pending_ids,
                "digest_substituted_ids": digest_substituted_ids,
                "relevance": [
                    {
                        "id": hm.id,
                        "turn_index": hm.turn_index,
                        "role": hm.message.role,
                        "score": round(float(hm.relevance_score), 4),
                    }
                    for hm in filtered
                ],
            },
        )
        return [chunk]

    def _msg_cost(self, hm: HistoryMessage) -> int:
        """单条 token 数: 优先落库携带值, 缺则实时算 (兼容存量数据)。"""
        if hm.token_count is not None:
            return hm.token_count
        return self._meter.count_messages([hm.message])

    async def _build_digest_lookup(
        self, messages: list[HistoryMessage], cap: int
    ) -> DigestLookup | None:
        """批量预取已缓存 digest (一次 IN 查询, 避免逐条查库)。

        只查可能超 cap 的候选 (携带 token_count 已知且 <= cap 的直接跳过),
        缩小 IN 查询规模。token_count 未知 (存量无列) 的保守纳入。
        无 digest_store / 无候选 / 查询失败时返回 None -> DigestPolicy 走结构化骨架兜底,
        由异步 context.digest 任务下一轮补上缓存。
        """
        if self._digest_store is None or not messages:
            return None
        ids = [
            hm.id for hm in messages
            if hm.token_count is None or hm.token_count > cap
        ]
        if not ids:
            return None
        try:
            return await self._digest_store.batch_get(ids)
        except Exception as exc:  # noqa: BLE001 — 缓存读失败软降级
            logger.warning("digest 缓存预取失败, 走结构化骨架兜底: %s", exc)
            return None

    # aborted / error 消息不应带入 LLM 上下文
    _SKIP_STATUSES = frozenset({"aborted", "error", "streaming"})

    @staticmethod
    def _rows_to_history_messages(
        rows, exclude: set[str]
    ) -> list[HistoryMessage]:
        """ChatMessageView -> HistoryMessage 列表.

        仅保留 role in (user, assistant)、content 非空、且 status 为正常终态。
        同一 user 消息及其 parent_id 指向该 user 的 assistant 消息共享 turn_index。
        """
        skip = HistoryProvider._SKIP_STATUSES
        out: list[HistoryMessage] = []
        turn_indexes: dict[str, int] = {}
        for r in rows:
            if r.id in exclude:
                continue
            if r.role not in ("user", "assistant"):
                continue
            if not r.content:
                continue
            if getattr(r, "status", None) in skip:
                continue
            turn_key = (
                r.id
                if r.role == "user"
                else (getattr(r, "parent_id", None) or r.id)
            )
            if turn_key not in turn_indexes:
                turn_indexes[turn_key] = len(turn_indexes)
            out.append(
                HistoryMessage(
                    message=Message(role=r.role, content=r.content),
                    id=r.id,
                    turn_index=turn_indexes[turn_key],
                    # 落库时算好的 content token 数 (存量无列时为 None, 下游回退实时算)
                    token_count=getattr(r, "token_count", None),
                )
            )
        return out
