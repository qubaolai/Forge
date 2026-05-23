"""ORM 通用 mixin 与表选项常量。"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from forge.utils.snowflake import new_snowflake_id

# 所有 MySQL 表统一 utf8mb4 + InnoDB.
MYSQL_TABLE_OPTS = {
    "mysql_charset": "utf8mb4",
    "mysql_engine": "InnoDB",
}


def table_args(*indexes, comment: str = ""):
    opts = {**MYSQL_TABLE_OPTS}
    if comment:
        opts["comment"] = comment
    return (*indexes, opts)


class TimestampMixin:
    """时间戳 mixin: created_at + updated_at。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )


class BigIntPKMixin:
    """Snowflake BIGINT 主键 + 时间戳。id 由应用层生成，非 DB 自增。"""

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=False,
        default=new_snowflake_id,
        comment="Snowflake 主键",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )


class SoftDeleteMixin:
    """软删除 mixin。"""

    is_deleted: Mapped[bool] = mapped_column(default=False, comment="是否已软删除")
