"""知识库文档父块表 — 父子检索的父块原文存储。"""

from sqlalchemy import JSON, BigInteger, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.mixins import BigIntPKMixin, table_args
from forge.utils.id_generator import new_id


class KbDocumentChunkOrm(Base, BigIntPKMixin):
    __tablename__ = "kb_document_chunks"

    chunk_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False,
        default=lambda: new_id("chunk"), comment="业务 ID: chunk_xxx"
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

    @property
    def business_id(self) -> str:
        return self.chunk_id

    __table_args__ = table_args(
        Index("ix_chunks_doc_seq", "document_id", "seq"),
        Index("ix_chunks_kb", "kb_id"),
        Index("ix_chunks_hash", "chunk_hash"),
        comment="知识库文档父块表",
    )
