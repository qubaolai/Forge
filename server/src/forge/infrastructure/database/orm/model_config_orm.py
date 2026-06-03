"""按调用协议拆分的模型配置表。"""

from sqlalchemy import JSON, BigInteger, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.mixins import BigIntPKMixin, table_args


class ChatModelConfigOrm(Base, BigIntPKMixin):
    __tablename__ = "chat_model_configs"

    model_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="→ models.id")
    context_window: Mapped[int] = mapped_column(Integer, nullable=False, default=128000)
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=4096)
    input_modalities: Mapped[list] = mapped_column(JSON, nullable=False, default=lambda: ["text"])
    output_modalities: Mapped[list] = mapped_column(JSON, nullable=False, default=lambda: ["text"])
    capabilities: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    thinking_options: Mapped[list | None] = mapped_column(JSON, nullable=True)
    provider_options: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = table_args(
        Index("ix_chat_model_configs_model", "model_id", unique=True),
        comment="Chat 模型调用配置",
    )


class EmbeddingModelConfigOrm(Base, BigIntPKMixin):
    __tablename__ = "embedding_model_configs"

    model_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="→ models.id")
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    batch_size: Mapped[int] = mapped_column(Integer, nullable=False)
    supported_dimensions: Mapped[list] = mapped_column(JSON, nullable=False)
    max_batch_size: Mapped[int] = mapped_column(Integer, nullable=False)
    input_modalities: Mapped[list] = mapped_column(JSON, nullable=False, default=lambda: ["text"])
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    retry_backoff: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    provider_options: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = table_args(
        Index("ix_embedding_model_configs_model", "model_id", unique=True),
        comment="Embedding 模型调用配置",
    )


class RerankerModelConfigOrm(Base, BigIntPKMixin):
    __tablename__ = "reranker_model_configs"

    model_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="→ models.id")
    timeout_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=5.0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    retry_backoff: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    truncation_strategy: Mapped[str] = mapped_column(String(32), nullable=False, default="tail")
    max_doc_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=4000)
    monitor_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.1)
    provider_options: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = table_args(
        Index("ix_reranker_model_configs_model", "model_id", unique=True),
        comment="Reranker 模型调用配置",
    )
