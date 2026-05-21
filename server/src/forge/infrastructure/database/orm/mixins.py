"""ORM 通用 mixin 与表选项常量。"""

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Mapped, mapped_column

# 所有 MySQL 表统一 utf8mb4 + InnoDB.
# 不显式指定 collation: 跟随数据库默认 (MySQL 8 默认 utf8mb4_0900_ai_ci),
# 避免新表显式 utf8mb4_unicode_ci 与已有旧表产生外键 collation 不一致冲突
# (pymysql errno 3780)。
MYSQL_TABLE_OPTS = {
    "mysql_charset": "utf8mb4",
    "mysql_engine": "InnoDB",
}


def table_args(*indexes, comment: str = ""):
    """合并 indexes 和 MySQL 表选项 (含表注释), 返回 __table_args__ 形态。

    用法:
        __table_args__ = table_args(
            Index("ix_xxx", "col"),
            comment="用户表",
        )
    """
    opts = {**MYSQL_TABLE_OPTS}
    if comment:
        opts["comment"] = comment
    return (*indexes, opts)


class TimestampMixin:
    """时间戳 mixin: created_at + updated_at。"""

    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(),
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间",
    )


class SoftDeleteMixin:
    """软删除 mixin。"""

    is_deleted: Mapped[bool] = mapped_column(default=False, comment="是否已软删除")
