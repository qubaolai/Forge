"""RAG 向量索引批量重建任务。"""

from sqlalchemy import BigInteger, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.mixins import BigIntPKMixin, table_args


class RagIndexRebuildJobOrm(Base, BigIntPKMixin):
    __tablename__ = "rag_index_rebuild_jobs"

    model_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="目标 embedding models.id")
    binding_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    total_documents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    succeeded_documents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_documents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="→ users.id")

    __table_args__ = table_args(
        Index("ix_rag_rebuild_jobs_status", "status"),
        comment="RAG 向量索引批量重建任务",
    )
