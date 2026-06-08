"""数据库:engine + session 工厂 + 健康检查 + lifespan 钩子。

函数式 API,模块级全局单例。

设计决定:
- 不封装 Database 类。engine 全局只有一个,函数式更符合 Python 风格
- 单机模式默认 SQLite, 启动期通过 bootstrap_schema 创建最小 schema
"""

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager

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
    ("kb_documents", "embedding_model_id", "BIGINT NULL"),
    ("kb_documents", "vector_index_status", "VARCHAR(16) NOT NULL DEFAULT 'stale'"),
    ("kb_documents", "vector_index_error", "TEXT NULL"),
    ("kb_documents", "vector_indexed_at", "DATETIME NULL"),
    ("embedding_model_configs", "supported_dimensions", "JSON NULL"),
    ("embedding_model_configs", "max_batch_size", "INTEGER NULL"),
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


def _migrate_model_configs(sync_conn) -> None:
    """把旧 models 扁平字段幂等迁移到按调用类型配置表。"""
    from sqlalchemy import inspect, select, update

    from forge.infrastructure.database.orm.model_config_orm import (
        ChatModelConfigOrm,
        EmbeddingModelConfigOrm,
        RerankerModelConfigOrm,
    )
    from forge.infrastructure.database.orm.model_orm import ModelOrm
    from forge.infrastructure.database.orm.system_model_binding_orm import (
        SystemModelBindingOrm,
    )

    tables = set(inspect(sync_conn).get_table_names())
    required = {
        "models",
        "chat_model_configs",
        "embedding_model_configs",
        "reranker_model_configs",
        "system_model_bindings",
        "model_chains",
    }
    if not required.issubset(tables):
        return

    models = sync_conn.execute(select(ModelOrm.__table__)).mappings().all()
    existing_chat = set(sync_conn.execute(select(ChatModelConfigOrm.model_id)).scalars())
    existing_embedding = set(sync_conn.execute(select(EmbeddingModelConfigOrm.model_id)).scalars())
    existing_reranker = set(sync_conn.execute(select(RerankerModelConfigOrm.model_id)).scalars())

    for row in models:
        model_id = row["id"]
        model_type = "chat" if row["model_type"] == "text" else row["model_type"]
        extra = dict(row.get("extra_params") or {})
        if model_type == "chat" and model_id not in existing_chat:
            caps = []
            if row.get("supports_tools"):
                caps.append("tools")
            if row.get("supports_images"):
                caps.append("vision")
            if row.get("supports_thinking"):
                caps.append("thinking")
            sync_conn.execute(ChatModelConfigOrm.__table__.insert().values(
                model_id=model_id,
                context_window=row.get("context_window") or 128000,
                max_output_tokens=row.get("max_output_tokens") or 4096,
                input_modalities=["text", "image"] if row.get("supports_images") else ["text"],
                output_modalities=["text"],
                capabilities=caps,
                thinking_options=row.get("thinking_options"),
                provider_options=extra or None,
            ))
        elif model_type == "embedding" and model_id not in existing_embedding:
            dimension = int(extra.pop("dimension", 1024))
            batch_size = int(extra.pop("batch_size", 10))
            sync_conn.execute(EmbeddingModelConfigOrm.__table__.insert().values(
                model_id=model_id,
                dimension=dimension,
                batch_size=batch_size,
                supported_dimensions=[dimension],
                max_batch_size=batch_size,
                input_modalities=["text"],
                max_retries=int(extra.pop("max_retries", 3)),
                retry_backoff=float(extra.pop("retry_backoff", 1.0)),
                provider_options=extra or None,
            ))
        elif model_type == "reranker" and model_id not in existing_reranker:
            truncation = dict(extra.pop("truncation", {}) or {})
            sync_conn.execute(RerankerModelConfigOrm.__table__.insert().values(
                model_id=model_id,
                timeout_seconds=float(extra.pop("timeout", 5.0)),
                max_retries=int(extra.pop("max_retries", 2)),
                retry_backoff=float(extra.pop("retry_backoff", 1.0)),
                truncation_strategy=truncation.pop("strategy", "tail"),
                max_doc_chars=int(truncation.pop("max_doc_chars", 4000)),
                monitor_threshold=float(truncation.pop("monitor_threshold", 0.1)),
                provider_options={**extra, **truncation} or None,
            ))

    embedding_configs = sync_conn.execute(
        select(EmbeddingModelConfigOrm.__table__)
    ).mappings().all()
    for row in embedding_configs:
        values = {}
        if not row.get("supported_dimensions"):
            values["supported_dimensions"] = [row["dimension"]]
        if row.get("max_batch_size") is None:
            values["max_batch_size"] = row["batch_size"]
        if values:
            sync_conn.execute(
                update(EmbeddingModelConfigOrm.__table__)
                .where(EmbeddingModelConfigOrm.model_id == row["model_id"])
                .values(**values)
            )

    sync_conn.execute(
        update(ModelOrm.__table__).where(ModelOrm.model_type == "text").values(model_type="chat")
    )
    existing_roles = set(sync_conn.execute(select(SystemModelBindingOrm.role)).scalars())
    for role in ("rag_embedding", "semantic_history_embedding", "rag_reranker"):
        if role not in existing_roles:
            sync_conn.execute(SystemModelBindingOrm.__table__.insert().values(role=role, version=0))

    _seed_tier_chains(sync_conn)


def _seed_tier_chains(sync_conn) -> None:
    """幂等播种档位链(fast/smart/strong)。

    初始值取 sys_config 的 model_profiles 默认 "provider:model";若该模型在 DB 中
    存在且启用则种成 1 元素链,否则种空链(运行时 resolver 退化到系统保底)。
    对话链(conversation)不在此播种,由管理端按需配置。
    """
    from sqlalchemy import select

    from forge.infrastructure.database.orm.model_chain_orm import ModelChainOrm
    from forge.infrastructure.database.orm.model_orm import ModelOrm
    from forge.infrastructure.database.orm.model_provider_orm import ProviderOrm

    existing = set(sync_conn.execute(
        select(ModelChainOrm.chain_key).where(ModelChainOrm.scope == "tier")
    ).scalars())

    # 已启用的 (provider_name, model_name) chat 模型集合
    prov_name_by_id = dict(sync_conn.execute(
        select(ProviderOrm.id, ProviderOrm.name).where(ProviderOrm.is_enabled == 1)
    ).all())
    enabled_pairs: set[tuple[str, str]] = set()
    for row in sync_conn.execute(select(ModelOrm.__table__)).mappings().all():
        if not row.get("is_enabled") or row.get("model_type") not in ("chat", "text"):
            continue
        pname = prov_name_by_id.get(row["provider_id"])
        if pname:
            enabled_pairs.add((pname, row["name"]))

    try:
        profiles = get_settings().agent_profiles.model_profiles.model_dump()
    except Exception:  # noqa: BLE001 — 配置不可用时退回类默认
        from forge.config.domains.agent_profiles import ModelProfiles
        profiles = ModelProfiles().model_dump()

    for tier in ("fast", "smart", "strong"):
        if tier in existing:
            continue
        ref = str(profiles.get(tier) or "")
        entries: list[dict] = []
        if ":" in ref:
            provider, model = ref.split(":", 1)
            if (provider, model) in enabled_pairs:
                entries = [{"provider": provider, "model": model}]
        sync_conn.execute(ModelChainOrm.__table__.insert().values(
            scope="tier", chain_key=tier, entries=entries or None, version=0,
        ))


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
        await conn.run_sync(_migrate_model_configs)


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("Engine 尚未初始化,请确认 lifespan 已启动")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Engine 尚未初始化,请确认 lifespan 已启动")
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """通用 DB 会话上下文:非 HTTP 路径(后台任务 / 服务 / 钩子)统一事务边界。

    与 FastAPI 的 get_db 同语义:
        - 正常退出 → commit;块内抛异常 → rollback 后向上抛。
        - Repo 内部只 flush 不 commit,事务边界由本上下文统一管理。
        - 只读块也可直接用:提交一个未改动的会话是 no-op,无副作用。

    用法::

        async with session_scope() as db:
            repo = ChatMessageRepository(db)
            ...

    取代散落各处的 ``factory = get_session_factory(); async with factory() as db``
    样板,避免提交 / 回滚语义在调用点各写一遍而漂移。
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖:为每个请求提供一个 session,事务边界同 session_scope。"""
    async with session_scope() as session:
        yield session
