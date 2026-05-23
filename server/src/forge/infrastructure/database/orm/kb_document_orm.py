"""知识库文档表 — KB 内单个文档 (含状态机和进度)。"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.mixins import BigIntPKMixin, table_args
from forge.utils.id_generator import new_id


class KbDocumentOrm(Base, BigIntPKMixin):
    __tablename__ = "kb_documents"

    doc_id: Mapped[str] = mapped_column(
        String(40), unique=True, nullable=False,
        default=lambda: new_id("doc"), comment="业务 ID: doc_xxx"
    )
    kb_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="→ knowledge_bases.id (应用层引用, 无 FK)"
    )
    name: Mapped[str] = mapped_column(String(512), nullable=False, comment="显示文件名")
    source: Mapped[str] = mapped_column(
        String(16), default="upload", nullable=False, comment="upload / url / api"
    )
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True, comment="原始 URL")
    storage_path: Mapped[str | None] = mapped_column(String(1024), nullable=True, comment="文件存储路径")
    mime_type: Mapped[str] = mapped_column(
        String(128), default="application/octet-stream", nullable=False, comment="MIME 类型"
    )
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False, comment="文件字节大小")
    content_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="SHA256 去重")
    status: Mapped[str] = mapped_column(
        String(16), default="pending", nullable=False,
        comment="pending / parsing / chunking / embedding / indexed / failed"
    )
    status_message: Mapped[str | None] = mapped_column(Text, nullable=True, comment="状态详情")
    progress: Mapped[int] = mapped_column(default=0, nullable=False, comment="进度 0-100")
    chunk_count: Mapped[int] = mapped_column(default=0, nullable=False, comment="切分后的分块数")
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="完成索引时间")

    @property
    def business_id(self) -> str:
        return self.doc_id

    __table_args__ = table_args(
        Index("ix_kb_docs_kb_created", "kb_id", "created_at"),
        Index("ix_kb_docs_status", "kb_id", "status"),
        Index("ix_kb_docs_hash", "content_hash"),
        comment="知识库文档表 (用户上传)",
    )
