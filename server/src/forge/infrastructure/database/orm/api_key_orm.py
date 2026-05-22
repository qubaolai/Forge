"""UserApiKey 表 — API Key 认证。"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.mixins import TimestampMixin, table_args
from forge.utils.id_generator import new_id


class UserApiKey(Base, TimestampMixin):
    """用户 API Key 表 — 用于 CLI / 第三方工具远程认证。"""

    __tablename__ = "user_api_keys"

    id: Mapped[str] = mapped_column(
        String(40),
        primary_key=True,
        default=lambda: new_id("apikey"),
        comment="API Key 记录 ID，形如 apikey_xxx",
    )
    user_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属用户 ID",
    )
    name: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        comment="Key 名称（用户自定义标签）",
    )
    key_hash: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        comment="API Key 的 SHA256 哈希，用于查找",
    )
    prefix: Mapped[str] = mapped_column(
        String(12),
        nullable=False,
        comment="Key 前缀，用于 UI 回显辨识（如 fk_a1b2c3d4）",
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        comment="最后一次使用时间",
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        comment="过期时间，NULL 表示永不过期",
    )
    is_revoked: Mapped[bool] = mapped_column(
        default=False,
        nullable=False,
        comment="是否已吊销",
    )

    # 关联 — 仅在认证时通过 joinedload 预加载，列表查询不需要
    user = relationship("UserOrm", lazy="raise", foreign_keys=[user_id])

    __table_args__ = table_args(
        Index("ix_api_key_hash", "key_hash"),
        Index("ix_api_key_user", "user_id"),
        comment="用户 API Key 表",
    )
