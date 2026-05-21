"""会话摘要表 - 每个 session 一条, 原地 upsert.

设计:
    - session_id 作为主键 (一个 session 一条摘要, 永远 upsert);
      下次重新生成时 version += 1.
    - 不使用外键约束: 应用层维护 session 与 summary 的关系;
      session 删除时由 SessionLog-backed repository 显式清理 summary.
    - covered_until_message_id 仅作记录, 不验证存在性 (无 FK).
"""

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.mixins import table_args


class SessionSummaryOrm(Base):
    """会话摘要 (session-scoped)."""

    __tablename__ = "session_summaries"

    session_id: Mapped[str] = mapped_column(
        String(40),
        primary_key=True,
        comment="会话 ID",
    )
    workspace_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
        comment="工作空间 ID; 为空时兼容旧的纯 session 摘要",
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        comment="摘要正文",
    )
    covered_until_message_id: Mapped[str | None] = mapped_column(
        String(40),
        nullable=True,
        comment="摘要覆盖到哪条消息为止; 再往后是原文 history",
    )
    token_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="摘要本身的 token 估算, 用于 ContextBuilder 预算控制",
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        comment="版本号, 每次重生成 +1 (原地覆盖, 不保留历史)",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间",
    )

    __table_args__ = table_args(
        Index("ix_summary_updated", "updated_at"),
        comment="会话摘要表 (一 session 一条, 原地 upsert)",
    )
