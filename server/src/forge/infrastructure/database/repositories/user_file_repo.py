"""UserFile 仓储 — 用户上传文件元数据 (user_files 表)。

与 ChatFileRepository (LLM 生成沙盒) 分离。ID 统一雪花主键, 对外以字符串 str(id)
暴露; owner_user_id / session_id / message_id 内部按 BIGINT 存储与查询。
文件真相内容在 UserUploadStorage。

关键点:
    - 上传时 session_id / message_id 为空, 发消息时 bind_session_and_message 回填。
    - list_orphans/delete_by_ids 支撑「长期未关联会话的孤儿文件」定期清理。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.infrastructure.database.orm.user_file_orm import UserFileOrm


def _to_int(value: str | int | None) -> int | None:
    """把对外 ID (str(雪花)) 解析为 BIGINT; 非法/空返回 None。"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class UserFileView:
    """用户上传文件对外视图。id / owner_user_id / session_id / message_id 均为 str(雪花) 或 None。"""

    id: str
    owner_user_id: str
    session_id: str | None
    message_id: str | None
    filename: str
    mime_type: str | None
    size_bytes: int
    content_hash: str | None
    storage_path: str
    created_at: datetime
    # 与 chat_files 对齐: 上传文件来源恒为 "upload" (前端文件卡片按 source 渲染)。
    source: str = "upload"


class UserFileRepository:
    """MySQL-backed 用户上传文件元数据存储。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def add(
        self,
        *,
        owner_user_id: str,
        filename: str,
        storage_path: str,
        size_bytes: int = 0,
        mime_type: str | None = None,
        content_hash: str | None = None,
        session_id: str | None = None,
        message_id: str | None = None,
    ) -> UserFileView:
        row = UserFileOrm(
            owner_user_id=_to_int(owner_user_id),
            session_id=_to_int(session_id),
            message_id=_to_int(message_id),
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

    async def get_by_id(self, file_id: str) -> UserFileView | None:
        fid = _to_int(file_id)
        if fid is None:
            return None
        res = await self.db.execute(select(UserFileOrm).where(UserFileOrm.id == fid))
        row = res.scalar_one_or_none()
        return self._to_view(row) if row else None

    async def bind_session_and_message(
        self, file_id: str, session_id: str, message_id: str, *, owner_user_id: str
    ) -> bool:
        """把上传文件回填到会话与产生它的 user 消息 (带 owner 校验)。"""
        fid = _to_int(file_id)
        sid = _to_int(session_id)
        mid = _to_int(message_id)
        uid = _to_int(owner_user_id)
        if fid is None or sid is None or mid is None or uid is None:
            return False
        res = await self.db.execute(select(UserFileOrm).where(UserFileOrm.id == fid))
        row = res.scalar_one_or_none()
        if row is None or row.owner_user_id != uid:
            return False
        row.session_id = sid
        row.message_id = mid
        await self.db.flush()
        return True

    async def list_by_session(self, session_id: str) -> list[UserFileView]:
        sid = _to_int(session_id)
        if sid is None:
            return []
        rows = (
            await self.db.execute(
                select(UserFileOrm)
                .where(UserFileOrm.session_id == sid)
                .order_by(UserFileOrm.id.asc())
            )
        ).scalars().all()
        return [self._to_view(r) for r in rows]

    async def delete_by_session(self, session_id: str) -> list[str]:
        """删除某会话名下全部上传文件元数据, 返回被删行的 storage_path 列表 (供物理删)。"""
        sid = _to_int(session_id)
        if sid is None:
            return []
        rows = (
            await self.db.execute(
                select(UserFileOrm).where(UserFileOrm.session_id == sid)
            )
        ).scalars().all()
        paths = [r.storage_path for r in rows]
        if rows:
            await self.db.execute(
                delete(UserFileOrm).where(UserFileOrm.session_id == sid)
            )
            await self.db.flush()
        return paths

    async def list_orphans(self, cutoff: datetime) -> list[UserFileView]:
        """孤儿文件: session_id 为空且创建于 cutoff 之前 (从未发送过)。"""
        rows = (
            await self.db.execute(
                select(UserFileOrm).where(
                    UserFileOrm.session_id.is_(None),
                    UserFileOrm.created_at < cutoff,
                )
            )
        ).scalars().all()
        return [self._to_view(r) for r in rows]

    async def delete_by_ids(self, file_ids: list[str]) -> int:
        ids = [i for i in (_to_int(x) for x in file_ids) if i is not None]
        if not ids:
            return 0
        res = await self.db.execute(
            delete(UserFileOrm).where(UserFileOrm.id.in_(ids))
        )
        await self.db.flush()
        return res.rowcount or 0

    # ---- private ----
    @staticmethod
    def _to_view(row: UserFileOrm) -> UserFileView:
        return UserFileView(
            id=str(row.id),
            owner_user_id=str(row.owner_user_id),
            session_id=str(row.session_id) if row.session_id is not None else None,
            message_id=str(row.message_id) if row.message_id is not None else None,
            filename=row.filename,
            mime_type=row.mime_type,
            size_bytes=row.size_bytes or 0,
            content_hash=row.content_hash,
            storage_path=row.storage_path,
            created_at=row.created_at or datetime.now(UTC),
        )


__all__ = ["UserFileRepository", "UserFileView"]
