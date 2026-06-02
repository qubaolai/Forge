"""Provider ORM — LLM 供应商表。"""

from sqlalchemy import JSON, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.mixins import BigIntPKMixin, table_args


class ProviderOrm(Base, BigIntPKMixin):
    """LLM 供应商表。id 为雪花主键, 即对外唯一 ID (str(id))。"""

    __tablename__ = "providers"

    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="anthropic / openai / deepseek / dashscope")
    impl: Mapped[str] = mapped_column(String(64), nullable=True, comment="SDK 实现类名")
    base_url: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="API 地址, NULL=官方默认")
    is_enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    routing_config: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="fallback 图谱 / 重试策略")

    __table_args__ = table_args(
        Index("ix_prov_name", "name"),
        Index("ix_prov_enabled", "is_enabled"),
        comment="LLM 供应商表",
    )
