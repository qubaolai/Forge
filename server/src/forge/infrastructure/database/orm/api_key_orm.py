"""UserApiKey 表 — API Key 认证。"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.mixins import BigIntPKMixin, table_args


class UserApiKey(Base, BigIntPKMixin):
    """用户 API Key 表 — 用于 CLI / 第三方工具远程认证。

    id 为雪花主键, 即对外唯一 ID。
    user_id 引用 users.id (BIGINT)，应用层维护引用完整性，无 FK 约束。
    """

    __tablename__ = "user_api_keys"

    user_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="→ users.id (应用层引用, 无 FK)"
    )
    name: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="Key 名称（用户自定义标签）"
    )
    key_hash: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, comment="API Key 的 SHA256 哈希"
    )
    prefix: Mapped[str] = mapped_column(
        String(12), nullable=False, comment="Key 前缀，用于 UI 回显辨识（如 fk_a1b2c3d4）"
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="最后一次使用时间"
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="过期时间，NULL 表示永不过期"
    )
    is_revoked: Mapped[bool] = mapped_column(
        default=False, nullable=False, comment="是否已吊销"
    )

    __table_args__ = table_args(
        Index("ix_api_key_hash", "key_hash"),
        Index("ix_api_key_user", "user_id"),
        comment="用户 API Key 表",
    )
