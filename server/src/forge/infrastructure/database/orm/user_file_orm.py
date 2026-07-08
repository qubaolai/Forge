"""UserFile ORM — 用户上传文件元数据表。

与 chat_files (LLM 生成沙盒文件) 彻底分离, 这是「两个层面的业务」:
    - user_files:  用户主动上传的附件, 落在 UserUploadStorage
                   (user_uploads/<user_id>/<date>/<filename>), 与会话解耦。
    - chat_files:  LLM 通过 write_file 产出的代码文件, 落在 WorkspaceStorage。

关键差异:
    - session_id / message_id 均可空: 上传时不绑会话, 发消息时回填。
    - 长期 session_id 为空的「孤儿文件」由后台定期清理。
真相文件落盘, 本表只存元数据 + storage_path 键。id 为雪花主键, 即对外唯一文件 ID。
"""

from sqlalchemy import BigInteger, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.mixins import BigIntPKMixin, table_args


class UserFileOrm(Base, BigIntPKMixin):
    """用户上传文件元数据表。id 即对外唯一标识 (str(id))。"""

    __tablename__ = "user_files"

    owner_user_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="→ users.id, 鉴权归属"
    )
    session_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="→ chat_sessions.id, 上传时为空, 发消息时回填"
    )
    message_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="→ chat_messages.id, 绑定它的 user 消息"
    )
    filename: Mapped[str] = mapped_column(
        String(512), nullable=False, comment="展示文件名"
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
        String(1024), nullable=False, comment="UserUploadStorage 相对键"
    )

    __table_args__ = table_args(
        Index("ix_uf_owner", "owner_user_id"),
        Index("ix_uf_session", "session_id"),
        comment="用户上传文件元数据表",
    )
