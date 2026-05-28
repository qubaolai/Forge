"""ChatSession 仓储 — MySQL 实现 SessionStore Protocol。

会话 ID 使用业务 ID (sess_xxx)。user_id 列存储用户业务 ID (user_xxx)。
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from forge.infrastructure.database.orm.chat_session_orm import ChatSessionOrm
from forge.infrastructure.storage.data_protocols import SessionView


class ChatSessionRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_by_user(
        self, user_id: str, page: int, page_size: int, q: str = ""
    ) -> tuple[Sequence[SessionView], int]:
        stmt = select(ChatSessionOrm).where(
            ChatSessionOrm.status == "active",
            ChatSessionOrm.user_id == user_id,
        )
        cnt = select(func.count(ChatSessionOrm.id)).where(
            ChatSessionOrm.status == "active",
            ChatSessionOrm.user_id == user_id,
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
        res = await self.db.execute(
            select(ChatSessionOrm).where(ChatSessionOrm.session_id == session_id)
        )
        row = res.scalar_one_or_none()
        return self._to_view(row) if row else None

    async def create(self, *, user_id: str, title: str | None = None) -> SessionView:
        row = ChatSessionOrm(user_id=user_id, title=title or "")
        self.db.add(row)
        await self.db.flush()
        await self.db.refresh(row)
        return self._to_view(row)

    async def update_title(self, session: SessionView, title: str) -> SessionView:
        await self.db.execute(
            update(ChatSessionOrm)
            .where(ChatSessionOrm.session_id == session.id)
            .values(title=title, updated_at=datetime.now(UTC))
        )
        await self.db.flush()
        return await self.get_by_id(session.id)

    async def delete(self, session: SessionView) -> None:
        await self.db.execute(
            update(ChatSessionOrm)
            .where(ChatSessionOrm.session_id == session.id)
            .values(status="deleted", updated_at=datetime.now(UTC))
        )
        await self.db.flush()

    @staticmethod
    def _to_view(row: ChatSessionOrm) -> SessionView:
        return SessionView(
            id=row.session_id,
            user_id=row.user_id,
            agent_id="",
            title=row.title,
            message_count=0,
            last_message_at=row.updated_at,
            created_at=row.created_at or datetime.now(UTC),
            updated_at=row.updated_at or datetime.now(UTC),
            run_ids=[],
        )
