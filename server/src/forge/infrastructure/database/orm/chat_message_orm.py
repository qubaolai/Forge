"""ChatMessage ORM — 聊天消息表。"""

from sqlalchemy import JSON, BigInteger, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.mixins import BigIntPKMixin, table_args


class ChatMessageOrm(Base, BigIntPKMixin):
    """聊天消息表。id 为雪花主键, 即对外唯一消息 ID (str(id))。"""

    __tablename__ = "chat_messages"

    session_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="→ chat_sessions.id")
    role: Mapped[str] = mapped_column(String(16), nullable=False, comment="user / assistant / system")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="消息文本")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="done",
        comment="pending / streaming / done / error / partial / aborted",
    )
    parent_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="→ chat_messages.id")
    tool_calls: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="工具调用记录")
    citations: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="引用列表")
    usage: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="token 用量")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True, comment="错误信息")
    reasoning_content: Mapped[str | None] = mapped_column(Text, nullable=True, comment="思考链文本")
    reasoning_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="思考耗时 ms")
    context_meta: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="上下文元信息")
    # content 的 token 数, 落库算一次; 上下文组装热路径读它免重复 tiktoken, 缺则实时算
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="content token 数")

    __table_args__ = table_args(
        Index("ix_cm_session", "session_id"),
        Index("ix_cm_parent", "parent_id"),
        Index("ix_cm_role_status", "role", "status"),
        Index("ix_cm_created", "created_at"),
        comment="聊天消息表",
    )
