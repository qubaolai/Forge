"""知识库表 — 用户面向的 RAG 知识集合。"""

from sqlalchemy import JSON, BigInteger, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.mixins import BigIntPKMixin, table_args


class KnowledgeBaseOrm(Base, BigIntPKMixin):
    """知识库 (KB) 元数据表。id 为雪花主键, 即对外唯一 ID (str(id))。"""

    __tablename__ = "knowledge_bases"

    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="知识库名称")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="描述")
    visibility: Mapped[str] = mapped_column(
        String(16), default="private", nullable=False, comment="private / workspace / public"
    )
    owner_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="→ users.id (应用层引用, 无 FK)"
    )
    collaborators: Mapped[list | None] = mapped_column(
        JSON, nullable=True, comment="协作者列表"
    )
    embedding_model: Mapped[str] = mapped_column(
        String(128), default="", nullable=False, comment="embedding 模型标识"
    )
    chunk_size: Mapped[int] = mapped_column(default=512, nullable=False, comment="分块字符数")
    chunk_overlap: Mapped[int] = mapped_column(default=64, nullable=False, comment="分块重叠字符数")
    document_count: Mapped[int] = mapped_column(default=0, nullable=False, comment="文档总数")
    chunk_count: Mapped[int] = mapped_column(default=0, nullable=False, comment="分块总数")
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False, comment="文件总字节数")

    __table_args__ = table_args(
        Index("ix_kb_owner", "owner_id"),
        Index("ix_kb_visibility", "visibility"),
        comment="知识库 (KB) 元数据表",
    )
