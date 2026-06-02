"""ProviderKey ORM — 供应商 API Key 表。"""

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.mixins import BigIntPKMixin, table_args


class ProviderKeyOrm(Base, BigIntPKMixin):
    """供应商 API Key 表。id 为雪花主键, 即对外唯一 ID (str(id))。"""

    __tablename__ = "provider_keys"

    provider_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="→ providers.id")
    key_ciphertext: Mapped[str] = mapped_column(Text, nullable=False, comment="API Key (AES-256-GCM 加密)")
    key_fingerprint: Mapped[str] = mapped_column(String(12), nullable=False, comment="Key 指纹, 日志脱敏")
    is_enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="负载权重")
    cooldown_until: Mapped[str | None] = mapped_column(DateTime, nullable=True, comment="429 冷却到何时")
    failure_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_at: Mapped[str | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = table_args(
        Index("ix_pk_provider", "provider_id"),
        Index("ix_pk_enabled", "is_enabled"),
        comment="供应商 API Key 表",
    )
