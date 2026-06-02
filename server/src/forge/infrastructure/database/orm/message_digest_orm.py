"""MessageDigest ORM — 单条消息的 digest 缓存表.

归属: digest 是 context 子系统的能力, 本表只是其持久化后端 (ORM 统一落基础设施层)。
每条 assistant 长消息一行, 按 message_id 原地 upsert。
source_hash 用于判 stale (如 regenerate 重写了同一 message)。
"""

from sqlalchemy import JSON, BigInteger, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.mixins import BigIntPKMixin, table_args


class MessageDigestOrm(Base, BigIntPKMixin):
    __tablename__ = "message_digests"

    message_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False, comment="→ chat_messages.id (雪花)"
    )
    session_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True, comment="→ chat_sessions.id (雪花)"
    )
    segments: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list,
        comment="分段列表 [{kind,start_line,end_line,anchor,digest_text}]",
    )
    total_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="原文 token 估算"
    )
    source_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", comment="原文内容 hash, 判 stale"
    )
    model: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="生成 prose 摘要所用模型 (代码骨架为空)"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="done",
        comment="pending / done / failed",
    )

    __table_args__ = table_args(
        Index("ix_digest_session", "session_id"),
        Index("ix_digest_updated", "updated_at"),
        comment="消息 digest 缓存表 (一 message 一条, 按 message_id 原地 upsert)",
    )
