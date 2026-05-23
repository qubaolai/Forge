"""User 表 — 用户账号。"""

from sqlalchemy import Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.mixins import BigIntPKMixin, table_args
from forge.utils.id_generator import new_id


class UserOrm(Base, BigIntPKMixin):
    """用户账号表 — 系统登录主体。

    id         BIGINT Snowflake 主键 (内部 join 用)
    user_id    VARCHAR 业务 ID (外部引用, 如 JWT sub)
    """

    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(
        String(40),
        unique=True,
        nullable=False,
        default=lambda: new_id("user"),
        comment="业务 ID, 形如 user_xxx",
    )
    email: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, comment="登录邮箱, 唯一"
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="显示名称")
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="头像 URL")
    role: Mapped[str] = mapped_column(
        String(16), default="member", nullable=False, comment="角色: owner / admin / member / guest"
    )
    status: Mapped[str] = mapped_column(
        String(16), default="active", nullable=False, comment="状态: active / disabled / pending"
    )
    password_hash: Mapped[str] = mapped_column(Text, nullable=False, comment="bcrypt 哈希后的密码")

    @property
    def business_id(self) -> str:
        """对外使用的业务 ID (user_xxx)。"""
        return self.user_id

    __table_args__ = table_args(
        Index("ix_users_email", "email"),
        comment="用户账号表",
    )
