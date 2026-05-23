"""会话业务：校验归属、CRUD、列表。"""

from collections.abc import Sequence
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.dependencies import DbSession
from forge.core.exceptions import Forbidden, NotFound
from forge.infrastructure.database.repositories.chat_message_repo import (
    ChatMessageRepository,
)
from forge.infrastructure.database.repositories.chat_session_repo import (
    ChatSessionRepository,
)
from forge.infrastructure.storage.data_protocols import ChatMessageView, SessionView


class SessionService:
    def __init__(self, db: AsyncSession):
        self.session_repo = ChatSessionRepository(db)
        self.message_repo = ChatMessageRepository(db)

    async def list_for_user(
        self, user_id: str, page: int, page_size: int, q: str = ""
    ) -> tuple[list[dict], int]:
        items, total = await self.session_repo.list_by_user(user_id, page, page_size, q)
        return await self._enrich(items), total

    async def _enrich(self, sessions: Sequence[SessionView]) -> list[dict]:
        if not sessions:
            return []
        session_ids = [s.id for s in sessions]
        count_map = await self.message_repo.count_by_sessions(session_ids)
        last_map = await self.message_repo.latest_at_by_sessions(session_ids)
        out: list[dict] = []
        for s in sessions:
            out.append({
                "id": s.id,
                "user_id": s.user_id,
                "agent_id": s.agent_id or "default",
                "agent_name": "默认助手",
                "title": s.title,
                "message_count": count_map.get(s.id, 0),
                "last_message_at": last_map.get(s.id),
                "created_at": s.created_at,
                "updated_at": s.updated_at,
            })
        return out

    async def get_single_enriched(self, session: SessionView) -> dict:
        items = await self._enrich([session])
        return items[0]

    async def create(
        self, user_id: str, *, agent_id: str = "default", title: str | None = None
    ) -> SessionView:
        return await self.session_repo.create(user_id=user_id, agent_id=agent_id, title=title)

    async def get_owned(self, session_id: str, user_id: str) -> SessionView:
        session = await self.session_repo.get_by_id(session_id)
        if not session:
            raise NotFound("会话不存在", code=40410)
        if session.user_id != user_id:
            raise Forbidden("无权访问该会话", code=40310)
        return session

    async def rename(self, session: SessionView, title: str) -> SessionView:
        return await self.session_repo.update_title(session, title)

    async def delete(self, session: SessionView) -> None:
        await self.session_repo.delete(session)

    async def list_messages(
        self, session_id: str, page: int, page_size: int
    ) -> tuple[Sequence[ChatMessageView], int]:
        return await self.message_repo.list_by_session(session_id, page, page_size)


def get_session_service(db: DbSession) -> SessionService:
    return SessionService(db)


SessionServiceDep = Annotated[SessionService, Depends(get_session_service)]
