"""Model ORM — 统一模型注册表。"""


from sqlalchemy import JSON, BigInteger, Boolean, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.mixins import BigIntPKMixin, table_args


class ModelOrm(Base, BigIntPKMixin):
    """模型信息表。id 为雪花主键, 即对外唯一 ID (str(id))。"""

    __tablename__ = "models"

    provider_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="→ providers.id")
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="模型名: gpt-4o / qwen-plus / text-embedding-v3")
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="", comment="展示名")
    model_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="chat", comment="chat / embedding / reranker"
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, comment="启用标识")

    __table_args__ = table_args(
        Index("ix_models_provider", "provider_id"),
        Index("ix_models_type", "model_type"),
        Index("ix_models_enabled", "is_enabled"),
        Index("ix_models_biz_key", "provider_id", "model_type", "name", unique=True),
        Index("ix_models_type_enabled", "model_type", "is_enabled"),
        comment="模型信息表",
    )
