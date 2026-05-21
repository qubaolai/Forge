"""会话仓储 (SessionLog-backed 实现)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from config import paths
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.dependencies import DbSession
from forge.infrastructure.session_log import (
    SessionLog,
    SessionMeta,
    find_session_log_path,
    session_log_for,
)
from forge.utils.id_generator import new_id
from forge.workspace import load_workspace_context


@dataclass
class SessionView:
    id: str
    user_id: str
    agent_id: str
    title: str
    created_at: datetime
    updated_at: datetime


class SessionRepository:
    def __init__(self, db: AsyncSession):
        # 兼容旧注入签名; 文件化后不再依赖 DB 事务.
        self.db = db

    async def list_by_user(
        self, user_id: str, page: int, page_size: int, q: str = ""
    ) -> tuple[Sequence[SessionView], int]:
        _ = user_id  # 单机模式下忽略 user 过滤
        sessions: list[SessionView] = []
        for log_path in self._iter_session_log_paths():
            meta = await SessionLog(log_path).get_meta()
            if meta is None or meta.status == "deleted":
                continue
            if q and q not in (meta.title or ""):
                continue
            sessions.append(
                SessionView(
                    id=meta.session_id,
                    user_id=meta.user_id or "",
                    agent_id=meta.agent_id,
                    title=meta.title,
                    created_at=meta.created_at,
                    updated_at=meta.updated_at,
                )
            )

        sessions.sort(key=lambda s: s.updated_at, reverse=True)
        total = len(sessions)
        start = max(0, (page - 1) * page_size)
        end = start + page_size
        return sessions[start:end], total

    async def get_by_id(self, session_id: str) -> SessionView | None:
        meta = await self._log_for(session_id).get_meta()
        if meta is None or meta.status == "deleted":
            return None
        return SessionView(
            id=meta.session_id,
            user_id=meta.user_id or "",
            agent_id=meta.agent_id,
            title=meta.title,
            created_at=meta.created_at,
            updated_at=meta.updated_at,
        )

    async def create(
        self, *, user_id: str, agent_id: str = "default", title: str | None = None
    ) -> SessionView:
        now = datetime.now(UTC)
        session_id = new_id("sess")
        ws_root = self._current_workspace_root()
        meta = SessionMeta(
            session_id=session_id,
            user_id=user_id,
            workspace_path=str(ws_root) if ws_root is not None else None,
            agent_id=agent_id or "default",
            title=title or "新会话",
            status="active",
            created_at=now,
            updated_at=now,
        )
        log = session_log_for(session_id, workspace_root=ws_root)
        await log.init(meta)
        return SessionView(
            id=session_id,
            user_id=user_id,
            agent_id=meta.agent_id,
            title=meta.title,
            created_at=now,
            updated_at=now,
        )

    async def update_title(self, session: SessionView, title: str) -> SessionView:
        now = datetime.now(UTC)
        old = await self._log_for(session.id).get_meta()
        meta = SessionMeta(
            session_id=session.id,
            user_id=session.user_id,
            workspace_path=old.workspace_path if old is not None else None,
            agent_id=session.agent_id,
            title=title,
            status="active",
            created_at=session.created_at,
            updated_at=now,
        )
        await self._log_for(session.id).save_meta(meta)
        session.title = title
        session.updated_at = now
        return session

    async def delete(self, session: SessionView) -> None:
        # 1) 逻辑删 session meta
        now = datetime.now(UTC)
        log = self._log_for(session.id)
        old = await log.get_meta()
        meta = SessionMeta(
            session_id=session.id,
            user_id=session.user_id,
            workspace_path=old.workspace_path if old is not None else None,
            agent_id=session.agent_id,
            title=session.title,
            status="deleted",
            created_at=session.created_at,
            updated_at=now,
        )
        await log.save_meta(meta)

        # 2) 物理删 session 文件 (消息随会话清理)
        try:
            if log.path.exists():
                log.path.unlink()
        except Exception:
            # 兜底: 若物理删失败, 至少已逻辑删.
            pass

        # 3) 清理摘要 (软依赖: DB 不可用时只记日志, 不影响会话删除)
        try:
            from forge.infrastructure.database.database import get_session_factory
            from forge.memory.summary.store import SummaryStore

            await SummaryStore(get_session_factory()).delete(session.id)
        except Exception:
            pass

    @staticmethod
    def _iter_session_log_paths() -> list[Path]:
        projects = paths.projects_dir()
        if not projects.exists():
            return []
        return list(projects.glob("*/sessions/*.jsonl"))

    @staticmethod
    def _current_workspace_root() -> Path | None:
        ctx = load_workspace_context()
        if ctx.mode == "workspace":
            return ctx.root_path
        return None

    def _log_for(self, session_id: str) -> SessionLog:
        existing = find_session_log_path(session_id)
        if existing is not None:
            return SessionLog(existing)
        return session_log_for(session_id, workspace_root=self._current_workspace_root())


def get_session_repo(db: DbSession) -> SessionRepository:
    return SessionRepository(db)


SessionRepoDep = Annotated[SessionRepository, Depends(get_session_repo)]
