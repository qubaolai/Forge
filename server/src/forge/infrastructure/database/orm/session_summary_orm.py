"""会话摘要表 — 每个 session 一条，原地 upsert。"""

from sqlalchemy import BigInteger, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.mixins import BigIntPKMixin, table_args


class SessionSummaryOrm(Base, BigIntPKMixin):
    __tablename__ = "session_summaries"

    session_id: Mapped[str] = mapped_column(
        String(40), unique=True, nullable=False, comment="会话业务 ID (sess_xxx)"
    )
    workspace_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True, comment="工作空间 ID"
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="摘要正文")
    covered_until_message_id: Mapped[str | None] = mapped_column(
        String(40), nullable=True, comment="摘要覆盖到哪条消息为止"
    )
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="摘要 token 估算")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="版本号")

    __table_args__ = table_args(
        Index("ix_summary_updated", "updated_at"),
        comment="会话摘要表 (一 session 一条, 原地 upsert)",
    )
