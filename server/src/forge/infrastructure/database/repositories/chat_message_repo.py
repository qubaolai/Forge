"""ChatMessage 仓储 — MySQL 实现 MessageStore ABC。

ID 统一为雪花主键; 对外以字符串 (str(id)) 暴露, 内部按 BIGINT 查询。
session_id / parent_id 直接是雪花 FK, 无需业务 ID ↔ 主键的来回解析。
分页使用 cursor-based (WHERE id < ? ORDER BY id DESC)。
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from forge.infrastructure.database.orm.chat_message_orm import ChatMessageOrm
from forge.infrastructure.storage.data_protocols import ChatMessageView, MessageStore


def _to_int(value: str | int | None) -> int | None:
    """把对外 ID (str(雪花)) 解析为 BIGINT; 非法/空返回 None。"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class ChatMessageRepository(MessageStore):
    """MySQL-backed chat message storage，cursor-based 分页。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ---- MessageStore ABC ----
    async def list_by_session(
        self, session_id: str, page: int, page_size: int
    ) -> tuple[Sequence[ChatMessageView], int]:
        sid = _to_int(session_id)
        if sid is None:
            return [], 0
        stmt = (
            select(ChatMessageOrm)
            .where(ChatMessageOrm.session_id == sid)
            .order_by(ChatMessageOrm.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        cnt = select(func.count(ChatMessageOrm.id)).where(
            ChatMessageOrm.session_id == sid
        )
        items = (await self.db.execute(stmt)).scalars().all()
        total = (await self.db.execute(cnt)).scalar_one()
        return self._to_views(items), total

    async def load_recent(
        self, session_id: str, limit: int = 30
    ) -> list[ChatMessageView]:
        """加载最近消息，cursor-based：取最新 limit 条。"""
        sid = _to_int(session_id)
        if sid is None:
            return []
        stmt = (
            select(ChatMessageOrm)
            .where(ChatMessageOrm.session_id == sid)
            .order_by(ChatMessageOrm.id.desc())
            .limit(limit)
        )
        items = (await self.db.execute(stmt)).scalars().all()
        return self._to_views(list(reversed(items)))

    async def load_cursor_page(
        self, session_id: str, cursor: int | None = None, limit: int = 30
    ) -> tuple[list[ChatMessageView], bool]:
        """cursor-based 分页：cursor 为上一页最后一条消息的 id（BIGINT）。

        返回 (消息列表, has_more)。
        首页传 cursor=None，后续传上一页最后一条的 id。
        """
        sid = _to_int(session_id)
        if sid is None:
            return [], False

        stmt = select(ChatMessageOrm).where(ChatMessageOrm.session_id == sid)
        if cursor is not None:
            stmt = stmt.where(ChatMessageOrm.id < cursor)
        stmt = stmt.order_by(ChatMessageOrm.id.desc()).limit(limit + 1)

        items = (await self.db.execute(stmt)).scalars().all()
        has_more = len(items) > limit
        if has_more:
            items = items[:limit]
        return self._to_views(items), has_more

    async def add(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        status: str = "done",
        parent_id: str | None = None,
    ) -> ChatMessageView:
        row = ChatMessageOrm(
            session_id=_to_int(session_id),
            role=role,
            content=content,
            status=status,
            parent_id=_to_int(parent_id),
        )
        self.db.add(row)
        await self.db.flush()
        await self.db.refresh(row)
        return self._to_view(row)

    async def get_by_id(self, message_id: str) -> ChatMessageView | None:
        mid = _to_int(message_id)
        if mid is None:
            return None
        res = await self.db.execute(
            select(ChatMessageOrm).where(ChatMessageOrm.id == mid)
        )
        row = res.scalar_one_or_none()
        return self._to_view(row) if row else None

    async def count_by_session(self, session_id: str) -> int:
        sid = _to_int(session_id)
        if sid is None:
            return 0
        cnt = await self.db.execute(
            select(func.count(ChatMessageOrm.id)).where(
                ChatMessageOrm.session_id == sid
            )
        )
        return cnt.scalar_one()

    async def count_by_sessions(self, session_ids: list[str]) -> dict[str, int]:
        result: dict[str, int] = {}
        for bid in session_ids:
            result[bid] = await self.count_by_session(bid)
        return result

    async def latest_at_by_sessions(
        self, session_ids: list[str]
    ) -> dict[str, datetime]:
        result: dict[str, datetime] = {}
        for bid in session_ids:
            sid = _to_int(bid)
            if sid is None:
                continue
            res = await self.db.execute(
                select(func.max(ChatMessageOrm.created_at)).where(
                    ChatMessageOrm.session_id == sid
                )
            )
            ts = res.scalar_one_or_none()
            if ts:
                result[bid] = ts
        return result

    async def save(self, msg: ChatMessageView) -> ChatMessageView:
        return await self.update(
            msg,
            content=msg.content,
            status=msg.status,
            citations=msg.citations,
            tool_calls=msg.tool_calls,
            usage=msg.usage,
            error_message=msg.error_message,
            context_meta=msg.context_meta,
            reasoning_content=msg.reasoning_content,
            reasoning_duration_ms=msg.reasoning_duration_ms,
        )

    async def update(
        self,
        msg: ChatMessageView,
        *,
        content: str | None = None,
        status: str | None = None,
        citations: list | None = None,
        tool_calls: list | None = None,
        usage: dict | None = None,
        error_message: str | None = None,
        context_meta: dict | None = None,
        reasoning_content: str | None = None,
        reasoning_duration_ms: int | None = None,
    ) -> ChatMessageView:
        values: dict = {"updated_at": datetime.now(UTC)}
        if content is not None:
            values["content"] = content
        if status is not None:
            values["status"] = status
        if citations is not None:
            values["citations"] = citations
        if tool_calls is not None:
            values["tool_calls"] = tool_calls
        if usage is not None:
            values["usage"] = usage
        if error_message is not None:
            values["error_message"] = error_message
        if context_meta is not None:
            values["context_meta"] = context_meta
        if reasoning_content is not None:
            values["reasoning_content"] = reasoning_content
        if reasoning_duration_ms is not None:
            values["reasoning_duration_ms"] = reasoning_duration_ms

        await self.db.execute(
            update(ChatMessageOrm)
            .where(ChatMessageOrm.id == _to_int(msg.id))
            .values(**values)
        )
        await self.db.flush()
        updated = await self.get_by_id(msg.id)
        if updated is None:
            raise RuntimeError(f"message 更新后不存在: {msg.id}")
        return updated

    async def delete_by_id(self, message_id: str) -> bool:
        mid = _to_int(message_id)
        if mid is None:
            return False
        res = await self.db.execute(
            select(ChatMessageOrm).where(ChatMessageOrm.id == mid)
        )
        row = res.scalar_one_or_none()
        if row:
            await self.db.delete(row)
            await self.db.flush()
            return True
        return False

    # ---- private ----
    def _to_views(
        self, rows: Sequence[ChatMessageOrm]
    ) -> list[ChatMessageView]:
        """批量转换 ORM 行 → 视图。"""
        return [self._to_view(row) for row in rows]

    @staticmethod
    def _to_view(row: ChatMessageOrm) -> ChatMessageView:
        """单条 ORM 行 → 视图。id / session_id / parent_id 均为 str(雪花)。"""
        return ChatMessageView(
            id=str(row.id),
            session_id=str(row.session_id),
            role=row.role,
            content=row.content or "",
            status=row.status,
            parent_id=str(row.parent_id) if row.parent_id is not None else None,
            tool_calls=row.tool_calls or [],
            citations=row.citations or [],
            usage=row.usage or {},
            error_message=row.error_message,
            context_meta=row.context_meta or {},
            reasoning_content=row.reasoning_content,
            reasoning_duration_ms=row.reasoning_duration_ms,
            created_at=row.created_at or datetime.now(UTC),
            updated_at=row.updated_at or datetime.now(UTC),
        )
