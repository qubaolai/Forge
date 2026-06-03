"""系统模型角色绑定。"""

from sqlalchemy import BigInteger, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.mixins import BigIntPKMixin, table_args


class SystemModelBindingOrm(Base, BigIntPKMixin):
    __tablename__ = "system_model_bindings"

    role: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="→ models.id")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="→ users.id")

    __table_args__ = table_args(
        Index("ix_system_model_bindings_role", "role", unique=True),
        comment="系统模型角色绑定",
    )
