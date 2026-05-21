"""知识库文档分块表 - 父子检索流水线的"父块原文存储".

定位 (重构后):
    - 这张表是父子检索中"父块"的全文存储, 取代了旧的 parent_chunks 表
    - 子块只活在 Chroma (向量) 与 BM25 (倒排) 库外存储里, 只存 chunk_id 引用
    - 检索命中后, 通过本表回查父块完整内容返还给 LLM
    - 前端"引用面板"也用这里的 content 直接渲染

ID 命名约定:
    - 表内列名保留 `id` (符合 SQLAlchemy PK 命名习惯)
    - retriever / 工具层把它当 chunk_id 用 (语义上就是父块 ID)

冗余字段:
    - kb_id 冗余: 按 KB 批量删除时省一次 JOIN
    - header_path / chunk_hash / source_type 来自旧 parent_chunks, 检索结果展示与
      去重/版本对比都要用
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.mixins import table_args
from forge.utils.id_generator import new_id


class KbDocumentChunkOrm(Base):
    """KB 文档父块. 父子检索的父块原文存储."""

    __tablename__ = "kb_document_chunks"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("chunk"),
        comment="父块 ID (chunk_<random> 或 hash 派生), 与向量库 / BM25 中的 parent_chunk_id 对齐",
    )
    document_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("kb_documents.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属文档 ID",
    )
    kb_id: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        comment="所属知识库 ID (denormalized, 按 KB 删除时省一次 JOIN)",
    )
    seq: Mapped[int] = mapped_column(
        default=0,
        nullable=False,
        comment="分块在文档内的顺序 (从 0 起, 父块无严格顺序时填 0)",
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="父块原文文本",
    )
    header_path: Mapped[str] = mapped_column(
        String(1024),
        default="",
        nullable=False,
        comment="标题路径, 形如 '第一章 / 1.1 概述', 用于检索结果上下文展示",
    )
    chunk_hash: Mapped[str] = mapped_column(
        String(64),
        default="",
        nullable=False,
        comment="文本 MD5/SHA256 哈希, 用于去重 / 增量更新判定",
    )
    source_type: Mapped[str] = mapped_column(
        String(32),
        default="text",
        nullable=False,
        comment="来源类型: pdf / docx / md / html / text 等",
    )
    extra: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        comment="元数据 (JSON): page / section / 表格原文 URL 等",
    )
    token_count: Mapped[int] = mapped_column(
        default=0,
        nullable=False,
        comment="估算的 token 数 (预留, 当前不强制)",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        comment="创建时间",
    )

    __table_args__ = table_args(
        Index("ix_chunks_doc_seq", "document_id", "seq"),
        Index("ix_chunks_kb", "kb_id"),
        Index("ix_chunks_hash", "chunk_hash"),
        comment="知识库文档父块表 (父子检索的父块原文存储)",
    )
