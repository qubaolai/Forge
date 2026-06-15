"""会话业务：校验归属、CRUD、列表。"""

import logging
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

logger = logging.getLogger(__name__)


class SessionService:
    def __init__(self, db: AsyncSession):
        self.db = db
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
                "agent_id": "",
                "agent_name": "",
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

    async def create(self, user_id: str, *, title: str | None = None) -> SessionView:
        return await self.session_repo.create(user_id=user_id, title=title)

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
        await self._delete_summary(session.id)
        await self._delete_files(session)

    @staticmethod
    async def _delete_summary(session_id: str) -> None:
        """级联清理会话摘要 (best-effort): 摘要清理失败不阻断删除主流程.

        SummaryStore 自管 DB 会话且自行 commit, 与本请求事务解耦, 局部 import
        避免 api 层对 memory 模块的常驻依赖.
        """
        try:
            from forge.infrastructure.database.database import get_session_factory
            from forge.memory.summary.store import SummaryStore

            await SummaryStore(get_session_factory()).delete(session_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("会话摘要级联清理失败 session=%s: %s", session_id, exc)

    @staticmethod
    async def _delete_files(session: SessionView) -> None:
        """级联清理会话文件 (元数据 + 物理沙盒目录), best-effort: 失败不阻断删除主流程。"""
        try:
            from forge.infrastructure.database.database import session_scope
            from forge.infrastructure.database.repositories.chat_file_repo import (
                ChatFileRepository,
            )
            from forge.infrastructure.storage.workspace_storage import WorkspaceStorage

            async with session_scope() as db:
                await ChatFileRepository(db).delete_by_session(session.id)
            WorkspaceStorage().delete_session(session.user_id, session.id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("会话文件级联清理失败 session=%s: %s", session.id, exc)

    async def files_by_message(self, session_id: str) -> dict[str, list[dict]]:
        """按 message_id 分组会话文件 (供 MessageOut.files 历史回看填充)。"""
        from forge.infrastructure.database.repositories.chat_file_repo import (
            ChatFileRepository,
        )

        files = await ChatFileRepository(self.db).list_by_session(session_id)
        grouped: dict[str, list[dict]] = {}
        for f in files:
            if not f.message_id:
                continue
            grouped.setdefault(f.message_id, []).append(
                {
                    "id": f.id,
                    "name": f.filename,
                    "source": f.source,
                    "size_bytes": f.size_bytes,
                    "mime_type": f.mime_type,
                }
            )
        return grouped

    async def list_messages(
        self, session_id: str, page: int, page_size: int
    ) -> tuple[Sequence[ChatMessageView], int]:
        return await self.message_repo.list_by_session(session_id, page, page_size)


def get_session_service(db: DbSession) -> SessionService:
    return SessionService(db)


SessionServiceDep = Annotated[SessionService, Depends(get_session_service)]
