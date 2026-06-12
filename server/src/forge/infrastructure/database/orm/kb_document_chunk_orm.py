"""知识库文档父块表 — 父子检索的父块原文存储。"""

from sqlalchemy import JSON, BigInteger, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.mixins import TimestampMixin, table_args


class KbDocumentChunkOrm(Base, TimestampMixin):
    """知识库文档父块表。

    本表唯一 ID = 父块语义 chunk_id (形如 ``{doc_id}__p_x``, 由切分阶段生成),
    它是「MySQL 父块行 ↔ 向量/BM25 条目」的关联键 (子块在向量库里以
    parent_chunk_id 指回父块)。因此该 ID 是组合字符串, 而非雪花数值——
    覆盖 BigIntPKMixin 的雪花 id 为 String 主键 (在 MySQL 上 BIGINT 无法承载)。
    """

    __tablename__ = "kb_document_chunks"

    id: Mapped[str] = mapped_column(
        String(128), primary_key=True, comment="父块语义 ID: {doc_id}__p_x"
    )
    document_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="→ kb_documents.id (应用层引用, 无 FK)"
    )
    kb_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="→ knowledge_bases.id (反范式, 无 FK)"
    )
    seq: Mapped[int] = mapped_column(default=0, nullable=False, comment="分块顺序")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="父块原文文本")
    header_path: Mapped[str] = mapped_column(
        String(1024), default="", nullable=False, comment="标题路径"
    )
    chunk_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False, comment="文本哈希去重")
    source_type: Mapped[str] = mapped_column(
        String(32), default="text", nullable=False, comment="pdf / docx / md / html / text"
    )
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="元数据")
    token_count: Mapped[int] = mapped_column(default=0, nullable=False, comment="估算 token 数")

    __table_args__ = table_args(
        Index("ix_chunks_doc_seq", "document_id", "seq"),
        Index("ix_chunks_kb", "kb_id"),
        Index("ix_chunks_hash", "chunk_hash"),
        comment="知识库文档父块表",
    )
