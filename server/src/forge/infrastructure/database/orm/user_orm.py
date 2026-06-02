"""User 表 — 用户账号。"""

from sqlalchemy import Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.mixins import BigIntPKMixin, table_args


class UserOrm(Base, BigIntPKMixin):
    """用户账号表 — 系统登录主体。

    id 为雪花主键, 既是内部 join 用也是对外唯一 ID (JWT sub 即 str(id))。
    不再维护单独的业务前缀 ID。
    """

    __tablename__ = "users"

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
    def user_id(self) -> str:
        """对外唯一 ID = 雪花主键的字符串形式 (列已删除, 此处为只读便捷访问)。"""
        return str(self.id)

    __table_args__ = table_args(
        Index("ix_users_email", "email"),
        comment="用户账号表",
    )
