"""refresh token 黑名单表。

登出 / 主动失效 / 强制下线后, 把对应 refresh token 的 jti 加入此表。
生产环境也可改用 Redis 实现, 此处 MySQL 版本作为持久兜底。
"""

from datetime import datetime

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.mixins import table_args


class RefreshTokenBlacklist(Base):
    """已撤销的 refresh token (按 jti 索引)。"""

    __tablename__ = "refresh_token_blacklist"

    jti: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        comment="JWT ID, refresh token 唯一标识",
    )
    user_id: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        comment="所属用户 ID",
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        comment="原 token 过期时间, 用于定期清理已自然过期的黑名单条目",
    )

    __table_args__ = table_args(
        Index("ix_blacklist_user", "user_id"),
        Index("ix_blacklist_expires", "expires_at"),
        comment="已撤销的 refresh token 黑名单",
    )
