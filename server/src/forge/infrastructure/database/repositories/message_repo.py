"""消息仓储 (SessionLog-backed 实现).

保留原 ``MessageRepository`` 方法签名, 但底层从 SQL 表切换为
``infrastructure.session_log.SessionLog``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from config import paths
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.dependencies import DbSession
from forge.infrastructure.session_log import (
    MessageRecord,
    SessionLog,
    SessionMeta,
    find_session_log_path,
    session_log_for,
)
from forge.utils.id_generator import new_id
from forge.workspace import load_workspace_context


@dataclass
class ChatMessageView:
    """兼容旧 ORM 对象访问方式的轻量消息视图."""

    id: str
    session_id: str
    role: str
    content: str
    status: str
    citations: Any | None = None
    tool_calls: Any | None = None
    usage: dict[str, Any] | None = None
    context_meta: dict[str, Any] | None = None
    parent_id: str | None = None
    error_message: str | None = None
    reasoning_content: str | None = None
    reasoning_duration_ms: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


def _to_view(session_id: str, rec: MessageRecord) -> ChatMessageView:
    return ChatMessageView(
        id=rec.id,
        session_id=session_id,
        role=rec.role,
        content=rec.content,
        status=rec.status,
        citations=rec.citations,
        tool_calls=rec.tool_calls,
        usage=rec.usage,
        context_meta=rec.context_meta,
        parent_id=rec.parent_id,
        error_message=rec.error_message,
        reasoning_content=rec.reasoning_content,
        reasoning_duration_ms=rec.reasoning_duration_ms,
        created_at=rec.created_at,
        updated_at=rec.updated_at,
    )


class MessageRepository:
    def __init__(self, db: AsyncSession):
        # 兼容旧注入签名; SessionLog 模式下不再依赖 DB.
        self.db = db
        self._session_cache: dict[str, SessionLog] = {}

    async def list_by_session(
        self, session_id: str, page: int, page_size: int
    ) -> tuple[Sequence[ChatMessageView], int]:
        log = self._log_for(session_id)
        msgs = await log.load_messages()
        views = [_to_view(session_id, m) for m in msgs if m.status != "deleted"]
        total = len(views)
        start = max(0, (page - 1) * page_size)
        end = start + page_size
        return views[start:end], total

    async def load_recent(self, session_id: str, limit: int = 30) -> list[ChatMessageView]:
        """加载最近 N 条已完成的消息(用于喂给 LLM)。"""
        log = self._log_for(session_id)
        rows = await log.load_recent(limit=limit, status="done")
        return [_to_view(session_id, r) for r in rows]

    async def add(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        status: str = "done",
        parent_id: str | None = None,
    ) -> ChatMessageView:
        now = datetime.now(UTC)
        log = self._log_for(session_id)
        if not await log.exists():
            ws_root = self._current_workspace_root()
            await log.init(
                SessionMeta(
                    session_id=session_id,
                    workspace_path=str(ws_root) if ws_root is not None else None,
                )
            )
        rec = MessageRecord(
            id=new_id("msg"),
            role=role,
            content=content,
            status=status,
            parent_id=parent_id,
            created_at=now,
            updated_at=now,
        )
        await log.append_message(rec)
        return _to_view(session_id, rec)

    async def get_by_id(self, message_id: str) -> ChatMessageView | None:
        for log_path in self._iter_session_log_paths():
            # 复用 cache, 避免重复构造.
            session_id = log_path.stem
            log = self._session_cache.get(session_id)
            if log is None:
                log = SessionLog(log_path)
                self._session_cache[session_id] = log
            rec = await log.get_message(message_id)
            if rec is None:
                continue
            if rec.status == "deleted":
                return None
            return _to_view(session_id, rec)
        return None

    async def count_by_session(self, session_id: str) -> int:
        """统计单个会话的消息数。"""
        log = self._log_for(session_id)
        msgs = await log.load_messages()
        return len([m for m in msgs if m.status != "deleted"])

    async def count_by_sessions(self, session_ids: list[str]) -> dict[str, int]:
        """批量统计每个会话的消息数 (返回 session_id -> count)。"""
        if not session_ids:
            return {}
        out: dict[str, int] = {}
        for sid in session_ids:
            out[sid] = await self.count_by_session(sid)
        return out

    async def latest_at_by_sessions(self, session_ids: list[str]) -> dict[str, datetime]:
        """批量取每个会话的最后一条消息时间。"""
        if not session_ids:
            return {}
        out: dict[str, datetime] = {}
        for sid in session_ids:
            log = self._log_for(sid)
            msgs = await log.load_messages()
            kept = [m for m in msgs if m.status != "deleted"]
            if kept:
                out[sid] = kept[-1].created_at
        return out

    async def save(self, msg: ChatMessageView) -> ChatMessageView:
        msg.updated_at = datetime.now(UTC)
        await self._log_for(msg.session_id).append_message(self._to_record(msg))
        return msg

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
        if content is not None:
            msg.content = content
        if status is not None:
            msg.status = status
        if citations is not None:
            msg.citations = citations
        if tool_calls is not None:
            msg.tool_calls = tool_calls
        if usage is not None:
            msg.usage = usage
        if error_message is not None:
            msg.error_message = error_message
        if context_meta is not None:
            msg.context_meta = context_meta
        if reasoning_content is not None:
            msg.reasoning_content = reasoning_content
        if reasoning_duration_ms is not None:
            msg.reasoning_duration_ms = reasoning_duration_ms
        msg.updated_at = datetime.now(UTC)
        await self._log_for(msg.session_id).append_message(self._to_record(msg))
        return msg

    async def delete_by_id(self, message_id: str) -> bool:
        """逻辑删除消息 (append tombstone), 返回是否命中."""
        msg = await self.get_by_id(message_id)
        if msg is None:
            return False
        msg.status = "deleted"
        msg.updated_at = datetime.now(UTC)
        await self._log_for(msg.session_id).append_message(self._to_record(msg))
        return True

    def _log_for(self, session_id: str) -> SessionLog:
        log = self._session_cache.get(session_id)
        if log is None:
            existing = find_session_log_path(session_id)
            if existing is not None:
                log = SessionLog(existing)
            else:
                log = session_log_for(
                    session_id,
                    workspace_root=self._current_workspace_root(),
                )
            self._session_cache[session_id] = log
        return log

    @staticmethod
    def _to_record(msg: ChatMessageView) -> MessageRecord:
        return MessageRecord(
            id=msg.id,
            role=msg.role,
            content=msg.content,
            status=msg.status,
            citations=msg.citations if isinstance(msg.citations, list) else msg.citations,
            tool_calls=msg.tool_calls if isinstance(msg.tool_calls, list) else msg.tool_calls,
            usage=msg.usage,
            context_meta=msg.context_meta,
            parent_id=msg.parent_id,
            error_message=msg.error_message,
            reasoning_content=msg.reasoning_content,
            reasoning_duration_ms=msg.reasoning_duration_ms,
            created_at=msg.created_at or datetime.now(UTC),
            updated_at=msg.updated_at or datetime.now(UTC),
        )

    @staticmethod
    def _iter_session_log_paths() -> list[Path]:
        roots: list[Path] = []
        projects = paths.projects_dir()
        if projects.exists():
            roots = list(projects.glob("*/sessions/*.jsonl"))
        return roots

    @staticmethod
    def _current_workspace_root() -> Path | None:
        ctx = load_workspace_context()
        if ctx.mode == "workspace":
            return ctx.root_path
        return None


def get_message_repo(db: DbSession) -> MessageRepository:
    return MessageRepository(db)


MessageRepoDep = Annotated[MessageRepository, Depends(get_message_repo)]
