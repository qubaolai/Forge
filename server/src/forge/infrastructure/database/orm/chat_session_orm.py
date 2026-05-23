"""ChatSession ORM — 聊天会话表。"""

from sqlalchemy import BigInteger, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.mixins import BigIntPKMixin, table_args
from forge.utils.id_generator import new_id


class ChatSessionOrm(Base, BigIntPKMixin):
    __tablename__ = "chat_sessions"

    session_id: Mapped[str] = mapped_column(
        String(40), unique=True, nullable=False, default=lambda: new_id("sess"), comment="业务ID: sess_xxx"
    )
    user_id: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="用户业务 ID (user_xxx), 无 FK"
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
