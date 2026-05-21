"""会话业务:校验归属、CRUD、列表。

注:get_owned 是个核心规则,任何涉及"操作 session"的业务都要经过它。
"""

from collections.abc import Sequence
from typing import Annotated

from fastapi import Depends

from forge.core.exceptions import NotFound
from forge.infrastructure.database.repositories.agent_repo import (
    AgentRepoDep,
    AgentRepository,
)
from forge.infrastructure.database.repositories.message_repo import (
    ChatMessageView,
    MessageRepoDep,
    MessageRepository,
)
from forge.infrastructure.database.repositories.session_repo import (
    SessionRepoDep,
    SessionRepository,
    SessionView,
)


class SessionService:
    def __init__(
        self,
        session_repo: SessionRepository,
        message_repo: MessageRepository,
        agent_repo: AgentRepository,
    ):
        self.session_repo = session_repo
        self.message_repo = message_repo
        self.agent_repo = agent_repo

    # ---------- 会话 ----------
    async def list_for_user(
        self, user_id: str, page: int, page_size: int, q: str = ""
    ) -> tuple[list[dict], int]:
        """返回聚合好的会话列表 (含 agent_name / message_count / last_message_at)."""
        items, total = await self.session_repo.list_by_user(user_id, page, page_size, q)
        return await self._enrich(items), total

    async def _enrich(self, sessions: Sequence[SessionView]) -> list[dict]:
        if not sessions:
            return []
        agent_ids = list({s.agent_id for s in sessions if s.agent_id})
        agents = await self.agent_repo.get_by_ids(agent_ids)
        name_map = {a.id: a.name for a in agents}

        session_ids = [s.id for s in sessions]
        count_map = await self.message_repo.count_by_sessions(session_ids)
        last_map = await self.message_repo.latest_at_by_sessions(session_ids)

        out: list[dict] = []
        for s in sessions:
            out.append(
                {
                    "id": s.id,
                    "user_id": s.user_id,
                    "agent_id": s.agent_id,
                    "agent_name": name_map.get(s.agent_id, "默认助手"),
                    "title": s.title,
                    "message_count": count_map.get(s.id, 0),
                    "last_message_at": last_map.get(s.id),
                    "created_at": s.created_at,
                    "updated_at": s.updated_at,
                }
            )
        return out

    async def get_single_enriched(self, session: SessionView) -> dict:
        items = await self._enrich([session])
        return items[0]

    async def create(
        self,
        user_id: str,
        *,
        agent_id: str = "default",
        title: str | None = None,
    ) -> SessionView:
        return await self.session_repo.create(user_id=user_id, agent_id=agent_id, title=title)

    async def get_owned(self, session_id: str, user_id: str) -> SessionView:
        """获取会话并校验所有权,失败抛业务异常。"""
        session = await self.session_repo.get_by_id(session_id)
        if not session:
            raise NotFound("会话不存在", code=40410)
        return session

    async def rename(self, session: SessionView, title: str) -> SessionView:
        return await self.session_repo.update_title(session, title)

    async def delete(self, session: SessionView) -> None:
        await self.session_repo.delete(session)

    # ---------- 消息 ----------
    async def list_messages(
        self, session_id: str, page: int, page_size: int
    ) -> tuple[Sequence[ChatMessageView], int]:
        return await self.message_repo.list_by_session(session_id, page, page_size)


def get_session_service(
    session_repo: SessionRepoDep,
    message_repo: MessageRepoDep,
    agent_repo: AgentRepoDep,
) -> SessionService:
    return SessionService(session_repo, message_repo, agent_repo)


SessionServiceDep = Annotated[SessionService, Depends(get_session_service)]
