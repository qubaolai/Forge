"""知识库文档表 - 用户向 KB 上传 / URL 抓取的文档。

与旧的 ingest 管线 documents 表分开:
    - documents (旧): 本地批量 ingest, 文件路径来源, 全局唯一
    - kb_documents (新): 用户在 KB 中上传的文档, 含状态机和进度
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.mixins import TimestampMixin, table_args
from forge.utils.id_generator import new_id


class KbDocumentOrm(Base, TimestampMixin):
    """KB 内的单个文档 (含解析 / 向量化状态)。"""

    __tablename__ = "kb_documents"

    id: Mapped[str] = mapped_column(
        String(40),
        primary_key=True,
        default=lambda: new_id("doc"),
        comment="文档 ID, 形如 doc_xxx",
    )
    kb_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属知识库 ID",
    )
    name: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        comment="显示文件名",
    )
    source: Mapped[str] = mapped_column(
        String(16),
        default="upload",
        nullable=False,
        comment="来源: upload / url / api",
    )
    source_url: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
        comment="source=url 时的原始 URL",
    )
    storage_path: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
        comment="原始文件存储路径 / 对象存储 key",
    )
    mime_type: Mapped[str] = mapped_column(
        String(128),
        default="application/octet-stream",
        nullable=False,
        comment="MIME 类型",
    )
    size_bytes: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
        comment="文件字节大小",
    )
    content_hash: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment="文件内容哈希 (sha256), 用于去重",
    )

    # 状态机
    status: Mapped[str] = mapped_column(
        String(16),
        default="pending",
        nullable=False,
        comment="处理状态: pending / parsing / chunking / embedding / indexed / failed",
    )
    status_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="状态详情 / 失败原因",
    )
    progress: Mapped[int] = mapped_column(
        default=0,
        nullable=False,
        comment="处理进度 0-100",
    )

    # 统计
    chunk_count: Mapped[int] = mapped_column(
        default=0,
        nullable=False,
        comment="切分后的分块数",
    )

    indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        comment="完成索引时间",
    )

    __table_args__ = table_args(
        Index("ix_kb_docs_kb_created", "kb_id", "created_at"),
        Index("ix_kb_docs_status", "kb_id", "status"),
        Index("ix_kb_docs_hash", "content_hash"),
        comment="知识库文档表 (用户上传)",
    )
