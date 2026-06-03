"""MessageEmbedding ORM — 单条消息的向量缓存表 (语义历史召回用).

归属: 语义召回是 context 子系统能力 (短期、会话级), 本表是其持久化后端。
由冷路径任务 (context.embedding) 按 message_id 原地 upsert; EmbeddingScorer 读它,
避免每轮对全部候选历史重算 embedding。

model 列记录生成该向量的 embedding 模型; 模型切换后旧向量按「缓存未命中」处理 (保留消息),
不做跨模型混用。source_hash 判 stale (regenerate 重写同一 message)。
"""

from sqlalchemy import JSON, BigInteger, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.mixins import BigIntPKMixin, table_args


class MessageEmbeddingOrm(Base, BigIntPKMixin):
    __tablename__ = "message_embeddings"

    message_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False, comment="→ chat_messages.id (雪花)"
    )
    session_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True, comment="→ chat_sessions.id (雪花)"
    )
    model: Mapped[str] = mapped_column(
        String(128), nullable=False, default="", comment="生成向量的 embedding 模型标识"
    )
    dim: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="向量维度"
    )
    vector: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list, comment="向量 list[float]"
    )
    source_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", comment="原文内容 hash, 判 stale"
    )

    __table_args__ = table_args(
        Index("ix_msgemb_session", "session_id"),
        Index("ix_msgemb_updated", "updated_at"),
        comment="消息向量缓存表 (一 message 一条, 按 message_id 原地 upsert)",
    )
