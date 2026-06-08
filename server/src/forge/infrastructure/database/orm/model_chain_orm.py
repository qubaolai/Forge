"""模型调用链表 — 对话链(per-provider)+ 档位链(per-tier)。

两块区域用 scope 区分,共用一张表:
    - scope="conversation": chain_key = provider 名, entries 为该 provider 启用模型的有序链
      (web user_pin 命中后的同 provider 后备顺序)。
    - scope="tier": chain_key = fast / smart / strong, entries 可跨 provider
      (CLI / utility 的档位链)。

entries 为有序 JSON 数组 [{"provider": ..., "model": ...}],天然匹配前端拖拽 reorder。
保存时由 service 校验每条 (provider, model) 存在 + 启用 + 是 chat 模型。
"""

from sqlalchemy import JSON, BigInteger, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.mixins import BigIntPKMixin, table_args


class ModelChainOrm(Base, BigIntPKMixin):
    __tablename__ = "model_chains"

    scope: Mapped[str] = mapped_column(String(32), nullable=False, comment="conversation / tier")
    chain_key: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="provider 名(conversation)或 fast/smart/strong(tier)"
    )
    entries: Mapped[list | None] = mapped_column(
        JSON, nullable=True, comment='有序数组 [{"provider":..,"model":..}]'
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="→ users.id")

    __table_args__ = table_args(
        Index("ix_model_chains_scope_key", "scope", "chain_key", unique=True),
        comment="模型调用链(对话链 / 档位链)",
    )
