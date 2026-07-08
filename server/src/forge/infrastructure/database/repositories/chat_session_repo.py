"""ChatSession 仓储 — MySQL 实现 SessionStore ABC。

会话 ID 与 user_id 均为雪花 ID; 对外以字符串 (str(id)) 暴露, 内部按 BIGINT 查询。
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from forge.infrastructure.database.orm.chat_session_orm import ChatSessionOrm
from forge.infrastructure.storage.data_protocols import SessionStore, SessionView


def _to_int(value: str | int | None) -> int | None:
    """把对外 ID (str(雪花)) 解析为 BIGINT; 非法/空返回 None。"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class ChatSessionRepository(SessionStore):
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_by_user(
        self, user_id: str, page: int, page_size: int, q: str = ""
    ) -> tuple[Sequence[SessionView], int]:
        uid = _to_int(user_id)
        if uid is None:
            return [], 0
        stmt = select(ChatSessionOrm).where(
            ChatSessionOrm.status == "active",
            ChatSessionOrm.user_id == uid,
        )
        cnt = select(func.count(ChatSessionOrm.id)).where(
            ChatSessionOrm.status == "active",
            ChatSessionOrm.user_id == uid,
        )
        if q:
            stmt = stmt.where(ChatSessionOrm.title.contains(q))
            cnt = cnt.where(ChatSessionOrm.title.contains(q))
        stmt = stmt.order_by(ChatSessionOrm.updated_at.desc()).offset(
            (page - 1) * page_size
        ).limit(page_size)
        items = (await self.db.execute(stmt)).scalars().all()
        total = (await self.db.execute(cnt)).scalar_one()
        return [self._to_view(s) for s in items], total

    async def get_by_id(self, session_id: str) -> SessionView | None:
        sid = _to_int(session_id)
        if sid is None:
            return None
        res = await self.db.execute(
            select(ChatSessionOrm).where(ChatSessionOrm.id == sid)
        )
        row = res.scalar_one_or_none()
        return self._to_view(row) if row else None

    async def get_active_by_id(self, session_id: str) -> SessionView | None:
        """按 id 取未删除的会话; 已软删 (status='deleted') 一律视为不存在.

        对外 /sessions/{id} 详情/消息/改名/删除 走本方法, 避免返回被删除的会话。
        """
        sid = _to_int(session_id)
        if sid is None:
            return None
        res = await self.db.execute(
            select(ChatSessionOrm).where(
                ChatSessionOrm.id == sid,
                ChatSessionOrm.status != "deleted",
            )
        )
        row = res.scalar_one_or_none()
        return self._to_view(row) if row else None

    async def create(self, *, user_id: str, title: str | None = None) -> SessionView:
        row = ChatSessionOrm(user_id=_to_int(user_id), title=title or "")
        self.db.add(row)
        await self.db.flush()
        await self.db.refresh(row)
        return self._to_view(row)

    async def update_title(self, session: SessionView, title: str) -> SessionView:
        await self.db.execute(
            update(ChatSessionOrm)
            .where(ChatSessionOrm.id == _to_int(session.id))
            .values(title=title, updated_at=datetime.now(UTC))
        )
        await self.db.flush()
        updated = await self.get_by_id(session.id)
        if updated is None:
            raise RuntimeError(f"session 更新后不存在: {session.id}")
        return updated

    async def delete(self, session: SessionView) -> None:
        await self.db.execute(
            update(ChatSessionOrm)
            .where(ChatSessionOrm.id == _to_int(session.id))
            .values(status="deleted", updated_at=datetime.now(UTC))
        )
        await self.db.flush()

    @staticmethod
    def _to_view(row: ChatSessionOrm) -> SessionView:
        return SessionView(
            id=str(row.id),
            user_id=str(row.user_id),
            agent_id="",
            title=row.title,
            message_count=0,
            last_message_at=row.updated_at,
            created_at=row.created_at or datetime.now(UTC),
            updated_at=row.updated_at or datetime.now(UTC),
            run_ids=[],
        )
