"""ChatFile 仓储 — 会话文件元数据 (chat_files 表)。

ID 统一雪花主键, 对外以字符串 str(id) 暴露; owner_user_id / session_id /
message_id 内部按 BIGINT 存储与查询。文件真相内容在 WorkspaceStorage。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.infrastructure.database.orm.chat_file_orm import ChatFileOrm


def _to_int(value: str | int | None) -> int | None:
    """把对外 ID (str(雪花)) 解析为 BIGINT; 非法/空返回 None。"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class ChatFileView:
    """会话文件对外视图。id / owner_user_id / session_id / message_id 均为 str(雪花)。"""

    id: str
    owner_user_id: str
    session_id: str
    message_id: str | None
    source: str
    filename: str
    mime_type: str | None
    size_bytes: int
    content_hash: str | None
    storage_path: str
    created_at: datetime


class ChatFileRepository:
    """MySQL-backed 会话文件元数据存储。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def add(
        self,
        *,
        owner_user_id: str,
        session_id: str,
        source: str,
        filename: str,
        storage_path: str,
        size_bytes: int = 0,
        mime_type: str | None = None,
        content_hash: str | None = None,
        message_id: str | None = None,
    ) -> ChatFileView:
        row = ChatFileOrm(
            owner_user_id=_to_int(owner_user_id),
            session_id=_to_int(session_id),
            message_id=_to_int(message_id),
            source=source,
            filename=filename,
            mime_type=mime_type,
            size_bytes=size_bytes,
            content_hash=content_hash,
            storage_path=storage_path,
        )
        self.db.add(row)
        await self.db.flush()
        await self.db.refresh(row)
        return self._to_view(row)

    async def get_by_id(self, file_id: str) -> ChatFileView | None:
        fid = _to_int(file_id)
        if fid is None:
            return None
        res = await self.db.execute(select(ChatFileOrm).where(ChatFileOrm.id == fid))
        row = res.scalar_one_or_none()
        return self._to_view(row) if row else None

    async def list_by_session(
        self, session_id: str, message_id: str | None = None
    ) -> list[ChatFileView]:
        sid = _to_int(session_id)
        if sid is None:
            return []
        stmt = select(ChatFileOrm).where(ChatFileOrm.session_id == sid)
        mid = _to_int(message_id)
        if mid is not None:
            stmt = stmt.where(ChatFileOrm.message_id == mid)
        stmt = stmt.order_by(ChatFileOrm.id.asc())
        rows = (await self.db.execute(stmt)).scalars().all()
        return [self._to_view(r) for r in rows]

    async def bind_message(self, file_id: str, message_id: str) -> bool:
        """把上传附件绑定到产生它的 user 消息 (回填 message_id)。"""
        fid = _to_int(file_id)
        mid = _to_int(message_id)
        if fid is None or mid is None:
            return False
        res = await self.db.execute(select(ChatFileOrm).where(ChatFileOrm.id == fid))
        row = res.scalar_one_or_none()
        if row is None:
            return False
        row.message_id = mid
        await self.db.flush()
        return True

    async def delete_by_session(self, session_id: str) -> int:
        sid = _to_int(session_id)
        if sid is None:
            return 0
        res = await self.db.execute(
            delete(ChatFileOrm).where(ChatFileOrm.session_id == sid)
        )
        await self.db.flush()
        return res.rowcount or 0

    # ---- private ----
    @staticmethod
    def _to_view(row: ChatFileOrm) -> ChatFileView:
        return ChatFileView(
            id=str(row.id),
            owner_user_id=str(row.owner_user_id),
            session_id=str(row.session_id),
            message_id=str(row.message_id) if row.message_id is not None else None,
            source=row.source,
            filename=row.filename,
            mime_type=row.mime_type,
            size_bytes=row.size_bytes or 0,
            content_hash=row.content_hash,
            storage_path=row.storage_path,
            created_at=row.created_at or datetime.now(UTC),
        )


__all__ = ["ChatFileRepository", "ChatFileView"]
