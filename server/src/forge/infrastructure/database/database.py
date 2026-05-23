"""数据库:engine + session 工厂 + 健康检查 + lifespan 钩子。

函数式 API,模块级全局单例。

设计决定:
- 不封装 Database 类。engine 全局只有一个,函数式更符合 Python 风格
- 单机模式默认 SQLite, 启动期通过 bootstrap_schema 创建最小 schema
"""

from collections.abc import AsyncGenerator

from config.settings import get_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

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


async def bootstrap_schema() -> None:
    """启动期 schema bootstrap — 幂等执行 create_all，所有驱动通用。

    SQLAlchemy create_all 对已存在的表会跳过（不报错），
    新表自动创建，已有表不改动。
    """
    if _engine is None:
        raise RuntimeError("Engine 尚未初始化")
    import forge.infrastructure.database.orm  # noqa: F401 — 触发模型注册
    from forge.infrastructure.database.orm.base import Base

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


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
