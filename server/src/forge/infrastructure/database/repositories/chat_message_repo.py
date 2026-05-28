"""ChatMessage 仓储 — MySQL 实现 MessageStore Protocol。

分页使用 cursor-based (WHERE id < ? ORDER BY id DESC)。
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from forge.infrastructure.database.orm.chat_message_orm import ChatMessageOrm
from forge.infrastructure.database.orm.chat_session_orm import ChatSessionOrm
from forge.infrastructure.storage.data_protocols import ChatMessageView


class ChatMessageRepository:
    """MySQL-backed chat message storage，cursor-based 分页。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ---- 内部 helper ----
    async def _resolve_session_db_id(self, session_business_id: str) -> int:
        """将业务 session_id 转为 BIGINT PK（用于内部 join 和 cursor 索引）。"""
        res = await self.db.execute(
            select(ChatSessionOrm.id).where(
                ChatSessionOrm.session_id == session_business_id
            )
        )
        row = res.scalar_one_or_none()
        if row is None:
            raise ValueError(f"会话不存在: {session_business_id}")
        return row

    async def _session_db_id(self, session_business_id: str) -> int | None:
        """安全版本，会话不存在时返回 None。"""
        res = await self.db.execute(
            select(ChatSessionOrm.id).where(
                ChatSessionOrm.session_id == session_business_id
            )
        )
        return res.scalar_one_or_none()

    async def _resolve_parent_db_id(self, parent_business_id: str) -> int | None:
        """将业务 message_id 转为 BIGINT PK。"""
        res = await self.db.execute(
            select(ChatMessageOrm.id).where(
                ChatMessageOrm.message_id == parent_business_id
            )
        )
        return res.scalar_one_or_none()

    async def _resolve_parent_business_id(self, parent_db_id: int) -> str | None:
        """将 BIGINT PK 转为业务 message_id。"""
        res = await self.db.execute(
            select(ChatMessageOrm.message_id).where(
                ChatMessageOrm.id == parent_db_id
            )
        )
        row = res.scalar_one_or_none()
        return row

    async def _batch_resolve_business_ids(self, db_ids: set[int]) -> dict[int, str]:
        """批量将 BIGINT PK 转为业务 message_id。"""
        if not db_ids:
            return {}
        res = await self.db.execute(
            select(ChatMessageOrm.id, ChatMessageOrm.message_id).where(
                ChatMessageOrm.id.in_(db_ids)
            )
        )
        return {row.id: row.message_id for row in res.all()}

    # ---- MessageStore Protocol ----
    async def list_by_session(
        self, session_id: str, page: int, page_size: int
    ) -> tuple[Sequence[ChatMessageView], int]:
        sid = await self._resolve_session_db_id(session_id)
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
        views = await self._to_views(items, session_id)
        return views, total

    async def load_recent(
        self, session_id: str, limit: int = 30
    ) -> list[ChatMessageView]:
        """加载最近消息，cursor-based：取最新 limit 条。"""
        sid = await self._session_db_id(session_id)
        if sid is None:
            return []
        stmt = (
            select(ChatMessageOrm)
            .where(ChatMessageOrm.session_id == sid)
            .order_by(ChatMessageOrm.id.desc())
            .limit(limit)
        )
        items = (await self.db.execute(stmt)).scalars().all()
        return await self._to_views(reversed(items), session_id)

    async def load_cursor_page(
        self, session_id: str, cursor: int | None = None, limit: int = 30
    ) -> tuple[list[ChatMessageView], bool]:
        """cursor-based 分页：cursor 为上一页最后一条消息的 id（BIGINT）。

        返回 (消息列表, has_more)。
        首页传 cursor=None，后续传上一页最后一条的 id。
        """
        sid = await self._session_db_id(session_id)
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
        views = await self._to_views(items, session_id)
        return views, has_more

    async def add(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        status: str = "done",
        parent_id: str | None = None,
    ) -> ChatMessageView:
        sid = await self._resolve_session_db_id(session_id)
        parent_db_id: int | None = None
        if parent_id is not None:
            parent_db_id = await self._resolve_parent_db_id(parent_id)
        row = ChatMessageOrm(
            session_id=sid,
            role=role,
            content=content,
            status=status,
            parent_id=parent_db_id,
        )
        self.db.add(row)
        await self.db.flush()
        await self.db.refresh(row)
        return await self._to_view(row, session_id)

    async def get_by_id(self, message_id: str) -> ChatMessageView | None:
        # 这里要一并取出业务 session_id，避免 _to_view 默认空串导致上层续写链路查不到会话。
        res = await self.db.execute(
            select(ChatMessageOrm, ChatSessionOrm.session_id)
            .outerjoin(ChatSessionOrm, ChatSessionOrm.id == ChatMessageOrm.session_id)
            .where(ChatMessageOrm.message_id == message_id)
        )
        pair = res.first()
        if pair is None:
            return None
        row, session_business_id = pair
        return await self._to_view(row, session_business_id or "")

    async def count_by_session(self, session_id: str) -> int:
        sid = await self._session_db_id(session_id)
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
            sid = await self._session_db_id(bid)
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
            .where(ChatMessageOrm.message_id == msg.id)
            .values(**values)
        )
        await self.db.flush()
        return await self.get_by_id(msg.id)

    async def delete_by_id(self, message_id: str) -> bool:
        res = await self.db.execute(
            select(ChatMessageOrm).where(ChatMessageOrm.message_id == message_id)
        )
        row = res.scalar_one_or_none()
        if row:
            await self.db.delete(row)
            await self.db.flush()
            return True
        return False

    # ---- private ----
    async def _to_views(
        self, rows: Sequence[ChatMessageOrm], session_business_id: str = ""
    ) -> list[ChatMessageView]:
        """批量转换 ORM 行 → 视图，一次性解析所有 parent_id。"""
        # 收集需要解析的 parent_id
        parent_db_ids: set[int] = set()
        for row in rows:
            if row.parent_id is not None:
                parent_db_ids.add(row.parent_id)
        id_map = await self._batch_resolve_business_ids(parent_db_ids)

        views: list[ChatMessageView] = []
        for row in rows:
            parent_business_id: str | None = None
            if row.parent_id is not None:
                parent_business_id = id_map.get(row.parent_id)
            views.append(
                ChatMessageView(
                    id=row.message_id,
                    session_id=session_business_id,
                    role=row.role,
                    content=row.content or "",
                    status=row.status,
                    parent_id=parent_business_id,
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
            )
        return views

    async def _to_view(
        self, row: ChatMessageOrm, session_business_id: str = ""
    ) -> ChatMessageView:
        """单条 ORM 行 → 视图。"""
        parent_business_id: str | None = None
        if row.parent_id is not None:
            parent_business_id = await self._resolve_parent_business_id(row.parent_id)
        return ChatMessageView(
            id=row.message_id,
            session_id=session_business_id,
            role=row.role,
            content=row.content or "",
            status=row.status,
            parent_id=parent_business_id,
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
