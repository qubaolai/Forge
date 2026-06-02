"""ChatSession ORM — 聊天会话表。"""

from sqlalchemy import BigInteger, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.mixins import BigIntPKMixin, table_args


class ChatSessionOrm(Base, BigIntPKMixin):
    """聊天会话表。id 为雪花主键, 即对外唯一会话 ID (str(id))。"""

    __tablename__ = "chat_sessions"

    user_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="→ users.id (雪花, 应用层引用, 无 FK)"
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="", comment="会话标题")
    workspace_id: Mapped[str] = mapped_column(String(255), nullable=False, default="", comment="workspace 路径")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", comment="active / deleted"
    )

    __table_args__ = table_args(
        Index("ix_cs_user", "user_id"),
        Index("ix_cs_status", "status"),
        Index("ix_cs_updated", "updated_at"),
        comment="聊天会话表",
    )
