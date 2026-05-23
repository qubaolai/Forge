"""refresh token 黑名单表。

登出 / 失效后把对应 refresh token 的 jti 加入此表。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.mixins import BigIntPKMixin, table_args


class RefreshTokenBlacklist(Base, BigIntPKMixin):
    __tablename__ = "refresh_token_blacklist"

    jti: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, comment="JWT ID, refresh token 唯一标识"
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="→ users.id (应用层引用, 无 FK)"
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, comment="原 token 过期时间, 用于定期清理"
    )

    __table_args__ = table_args(
        Index("ix_blacklist_user", "user_id"),
        Index("ix_blacklist_expires", "expires_at"),
        comment="已撤销的 refresh token 黑名单",
    )
