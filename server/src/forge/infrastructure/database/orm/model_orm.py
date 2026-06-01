"""Model ORM — 模型信息表（统一存储 text / embedding / reranker 等类型）。"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.mixins import BigIntPKMixin, table_args
from forge.utils.id_generator import new_id


class ModelOrm(Base, BigIntPKMixin):
    __tablename__ = "models"

    model_id: Mapped[str] = mapped_column(
        String(40), unique=True, nullable=False, default=lambda: new_id("mdl"), comment="业务ID: mdl_xxx"
    )
    provider_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="→ providers.id")
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="模型名: gpt-4o / qwen-plus / text-embedding-v3")
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="", comment="展示名")
    model_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="text", comment="text / embedding / reranker / image / audio"
    )
    context_window: Mapped[int] = mapped_column(Integer, nullable=False, default=128000, comment="上下文窗口长度")
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=4096, comment="最大输出 token")
    supports_tools: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, comment="是否支持工具调用")
    supports_images: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="是否支持图片识别")
    supports_thinking: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="是否支持思考模式")
    thinking_options: Mapped[list | None] = mapped_column(
        JSON, nullable=True, comment='思考强度档位: ["standard","low","medium","high","xhigh"]'
    )
    extra_params: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="类型特定参数: dimension / batch_size / timeout / truncation ..."
    )
    cost_tier: Mapped[str] = mapped_column(String(16), nullable=False, default="mid", comment="cheap / mid / expensive")
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, comment="启用标识")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="是否该供应商的默认模型")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="同类型内优先级")
    is_stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="API 不再返回时标记")

    __table_args__ = table_args(
        Index("ix_models_provider", "provider_id"),
        Index("ix_models_type", "model_type"),
        Index("ix_models_enabled", "is_enabled"),
        Index("ix_models_biz_key", "provider_id", "model_type", "name", unique=True),
        Index("ix_models_type_enabled", "model_type", "is_enabled"),
        comment="模型信息表",
    )
