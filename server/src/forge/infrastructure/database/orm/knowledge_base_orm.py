"""知识库表 - 用户面向的 RAG 知识集合。

每个 KB 是一组共享 embedding 配置的文档集合, Agent 通过 kb_ids 挂载 KB
来获得检索能力。统计字段 (document_count / chunk_count / size_bytes) 由
后台异步任务定期刷新。
"""

from sqlalchemy import JSON, BigInteger, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.mixins import TimestampMixin, table_args
from forge.utils.id_generator import new_id


class KnowledgeBaseOrm(Base, TimestampMixin):
    """知识库元数据。"""

    __tablename__ = "knowledge_bases"

    id: Mapped[str] = mapped_column(
        String(40),
        primary_key=True,
        default=lambda: new_id("kb"),
        comment="知识库 ID, 形如 kb_xxx",
    )
    name: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        comment="知识库名称",
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="描述",
    )
    visibility: Mapped[str] = mapped_column(
        String(16),
        default="private",
        nullable=False,
        comment="可见性: private / workspace / public",
    )
    owner_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="拥有者用户 ID",
    )
    collaborators: Mapped[list | None] = mapped_column(
        JSON,
        nullable=True,
        comment="协作者列表 (JSON): [{user_id, user_name, permission}]",
    )

    # 索引配置 (KB 内所有文档共用)
    embedding_model: Mapped[str] = mapped_column(
        String(128),
        default="",
        nullable=False,
        comment="embedding 模型标识 (创建后不可改, 改了要重建索引)",
    )
    chunk_size: Mapped[int] = mapped_column(
        default=512,
        nullable=False,
        comment="分块字符数",
    )
    chunk_overlap: Mapped[int] = mapped_column(
        default=64,
        nullable=False,
        comment="分块重叠字符数",
    )

    # 统计 (异步任务刷新)
    document_count: Mapped[int] = mapped_column(
        default=0,
        nullable=False,
        comment="文档总数 (denormalized)",
    )
    chunk_count: Mapped[int] = mapped_column(
        default=0,
        nullable=False,
        comment="分块总数 (denormalized)",
    )
    size_bytes: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
        comment="原始文件总字节数 (denormalized)",
    )

    __table_args__ = table_args(
        Index("ix_kb_owner", "owner_id"),
        Index("ix_kb_visibility", "visibility"),
        comment="知识库 (KB) 元数据表",
    )
