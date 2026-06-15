"""ChatFile ORM — 会话沙盒文件元数据表。

承载两类会话文件:
    - source=upload:    用户上传的大段输入附件
    - source=generated: LLM 通过 write_file 工具产出的代码文件

真相文件落在 WorkspaceStorage (workspace/<user_id>/<session_id>/<relpath>),
本表只存元数据 + storage_path 键。id 为雪花主键, 即对外唯一文件 ID (str(id))。
生命周期随会话: 会话删除时级联清理 (见 SessionService.delete)。
"""

from sqlalchemy import BigInteger, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.mixins import BigIntPKMixin, table_args


class ChatFileOrm(Base, BigIntPKMixin):
    """会话文件元数据表。id 即对外唯一标识 (str(id))，无独立 file_id 字段。"""

    __tablename__ = "chat_files"

    owner_user_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="→ users.id, 鉴权归属"
    )
    session_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="→ chat_sessions.id"
    )
    message_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="→ chat_messages.id, 产生/绑定它的消息"
    )
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="upload / generated"
    )
    filename: Mapped[str] = mapped_column(
        String(512), nullable=False, comment="展示文件名 (可含相对路径)"
    )
    mime_type: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="MIME 类型"
    )
    size_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, comment="文件字节数"
    )
    content_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="SHA256 hex"
    )
    storage_path: Mapped[str] = mapped_column(
        String(1024), nullable=False, comment="WorkspaceStorage 相对键"
    )

    __table_args__ = table_args(
        Index("ix_cf_session", "session_id"),
        Index("ix_cf_owner", "owner_user_id"),
        Index("ix_cf_message", "message_id"),
        comment="会话沙盒文件元数据表",
    )
