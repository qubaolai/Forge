"""数据库:engine + session 工厂 + 健康检查 + lifespan 钩子。

函数式 API,模块级全局单例。

设计决定:
- 不封装 Database 类。engine 全局只有一个,函数式更符合 Python 风格
- 单机模式默认 SQLite, 启动期通过 bootstrap_schema 创建最小 schema
"""

from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from forge.config.settings import get_settings

# ---------- 全局引擎 / 会话工厂 ----------
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine() -> None:
    """lifespan 启动时调用。幂等。"""
    global _engine, _session_factory
    if _engine is not None:
        return
    settings = get_settings()
    if settings.db.is_sqlite:
        _engine = create_async_engine(
            url=settings.db.url,
            echo=settings.db.echo,
        )
    else:
        _engine = create_async_engine(
            url=settings.db.url,
            echo=settings.db.echo,
            pool_pre_ping=settings.db.pool_pre_ping,
            pool_recycle=settings.db.pool_recycle,
            pool_size=settings.db.pool_size,
            max_overflow=settings.db.max_overflow,
        )
    _session_factory = async_sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)


async def dispose_engine() -> None:
    """lifespan 关闭时调用。"""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


async def ping() -> None:
    """健康检查:启动时验证 DB 可达,失败抛异常服务起不来。"""
    if _engine is None:
        raise RuntimeError("Engine 尚未初始化")
    async with _engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


# 既有表的增量列 (create_all 只建新表、不改已存在表; 这些列要补 ALTER)。
# 形如 (表名, 列名, DDL 类型片段)。SQLite / MySQL 均接受 "INTEGER NULL"。
_ADDITIVE_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("chat_messages", "token_count", "INTEGER NULL"),
)


def _ensure_additive_columns(sync_conn) -> None:
    """对既有表补充新增列 (幂等)。

    create_all 不会给已存在的表加列, 而本项目无 Alembic, 故用 inspector 检测
    缺失列再 ALTER TABLE ADD COLUMN。基于「列是否存在」判断而非异常文本,
    SQLite / MySQL 通用。新表 (create_all 刚建全) 直接跳过。
    """
    from sqlalchemy import inspect

    insp = inspect(sync_conn)
    existing_tables = set(insp.get_table_names())
    for table, column, ddl_type in _ADDITIVE_COLUMNS:
        if table not in existing_tables:
            continue  # 新表由 create_all 建全, 无需补列
        cols = {c["name"] for c in insp.get_columns(table)}
        if column in cols:
            continue
        sync_conn.exec_driver_sql(
            f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"
        )


async def bootstrap_schema() -> None:
    """启动期 schema bootstrap — 幂等执行 create_all + 增量列 ALTER，所有驱动通用。

    SQLAlchemy create_all 对已存在的表会跳过（不报错），新表自动创建、已有表不改动；
    已有表的新增列由 _ensure_additive_columns 补 ALTER (无 Alembic 的工程取舍)。
    """
    if _engine is None:
        raise RuntimeError("Engine 尚未初始化")
    import forge.infrastructure.database.orm  # noqa: F401 — 触发模型注册
    from forge.infrastructure.database.orm.base import Base

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_additive_columns)


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("Engine 尚未初始化,请确认 lifespan 已启动")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Engine 尚未初始化,请确认 lifespan 已启动")
    return _session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖:为每个请求提供一个 session,自动管理事务。

    请求正常结束 → commit;抛异常 → rollback。
    Repo 内部只 flush 不 commit,事务边界由这里统一控制。
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
