"""应用生命周期管理.

启动策略 (分层 fail-fast vs 软降级):
    硬性依赖 (失败 → 服务拒绝启动):
        - Logging / Tracing 初始化
        - Database (本地 SQLite, bootstrap_schema 自动建表)
        - LLM 预热 (默认 provider 失败仍允许启动, 首次调用时再报)

    软依赖 (失败 → WARN 跳过):
        - TaskQueue (默认 LocalTaskQueue; 失败降级 NullTaskQueue)
        - EventBus + memory hooks
        - RAG 栈 (Tokenizer / Embedder / VectorStore / BM25 / Retriever)

    设计理由:
        - 项目同时承载 Agent (LLM + Tool) 和 RAG 两条能力线.
        - Agent 链路完全不依赖 RAG, 没装 chromadb/dashscope 不应阻断启动.
        - 想强制 RAG 必须可用, 设环境变量 STRICT_RAG=true.

    任何 RAG 组件初始化失败时, app.state.<name> 不会被设置;
    业务侧用 hasattr(app.state, "embedder") 判断可用性.
"""

from __future__ import annotations

import inspect
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from config.settings import get_settings

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)

STRICT_RAG = os.environ.get("STRICT_RAG", "").lower() in ("1", "true", "yes")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # ---------- 启动 ----------
    settings = get_settings()

    # 0. Logging + Tracing (硬性)
    from forge.observability.logging.logger import setup_logging
    from forge.observability.tracing.tracer import setup_tracing

    setup_logging(settings.app.log_level, json_format=False)
    tcfg = settings.observability.tracing
    setup_tracing(
        enabled=tcfg.enabled,
        exporter=tcfg.exporter,
        service_name=tcfg.service_name,
        otel_endpoint=tcfg.otel_endpoint,
        langfuse_public_key=tcfg.langfuse_public_key,
        langfuse_secret_key=tcfg.langfuse_secret_key,
        langfuse_host=tcfg.langfuse_host,
    )
    logger.info("服务启动中...")

    # 0.1 PromptRegistry (硬性: 启动期一次扫描 + 编译所有 prompts/*.j2)
    from forge.prompts import init_registry

    try:
        init_registry()
    except Exception:
        # 启动期挂掉极少见 (除非 prompts/ 目录缺失或模板语法错), 但仍允许服务起来:
        # render() 会走兜底字符串, 主流程不致崩溃.
        logger.exception("PromptRegistry 初始化失败, render() 将走兜底文本")

    # 1. Database (硬性)
    from forge.infrastructure.database import database as db_module

    db_module.init_engine()
    await db_module.ping()
    await db_module.bootstrap_schema()
    logger.info("数据库就绪: %s", settings.db.safe_url)

    # 2. TaskQueue (单机默认 LocalTaskQueue; 失败降级 NullTaskQueue)
    from forge.infrastructure.queue import init_task_queue

    enabled = settings.celery.enabled
    task_modules = settings.celery.task_modules
    task_queue = init_task_queue(
        enabled=enabled,
        broker_url=settings.celery.broker_url,
        backend_url=settings.celery.backend_url or settings.celery.broker_url,
        task_modules=task_modules,
    )
    logger.info(
        "任务队列开启: %s task_modules=%s",
        enabled,
        task_modules,
    )

    # 2.2 EventBus + memory hooks (软: hooks 失败仅日志)
    from forge.infrastructure.event_bus import get_event_bus
    from forge.memory.hooks import install_memory_hooks

    event_bus = get_event_bus()
    try:
        install_memory_hooks(
            event_bus,
            task_queue,
            every_n_turns=settings.memory.trigger.every_n_turns,
            enabled=settings.memory.enabled,
        )
    except Exception:  # noqa: BLE001
        logger.exception("memory hooks 安装失败, 摘要任务不会被派发")

    # 3. LLM 预热: 遍历所有配置 (provider, api_key) 组合
    #    - 每个 (impl, api_key) 都建一个 SDK client, 存进 LLMClientPool
    #    - 单个 api_key 失败不阻断, 只记录
    #    - 整体失败仍允许服务启动 (首次调用时再尝试)
    from forge.llm.client_pool import get_llm_pool

    llm_pool = get_llm_pool()
    warmed, failed = 0, 0
    for provider_name, pcfg in settings.llm.providers.items():
        impl = pcfg.impl or provider_name
        client_options = {"base_url": pcfg.base_url, "timeout": pcfg.timeout}
        for api_key in pcfg.api_keys:
            try:
                llm_pool.warm(impl, api_key, client_options)
                warmed += 1
            except Exception:
                logger.exception("LLM 预热失败: impl=%s key=%s***", impl, api_key[:6])
                failed += 1
    logger.info("LLM 池预热完成: 成功=%d 失败=%d 池容量=%d", warmed, failed, llm_pool.size())
    app.state.llm_pool = llm_pool

    # 3.1 CostTracker: 加载 budget 配置 + baseline hydrate
    #   - configure_budget 把 settings.llm.budget 推到 tracker (硬: 配错则启动失败)
    #   - hydrate_baseline 软依赖, 失败仅日志, 首日为空 baseline 行为正确
    try:
        from forge.llm.cost_tracker import BudgetConfig, get_cost_tracker
        from forge.quota import configure_usage_quota

        cost_tracker = get_cost_tracker()
        usage_quota = configure_usage_quota(settings)
        qcfg = settings.user_quota
        logger.info(
            "用户用量额度配置就绪 enabled=%s has_limits=%s five_hour=%s weekly=%s alert=%.2f",
            qcfg.enabled,
            qcfg.has_limits,
            qcfg.five_hour.model_dump(),
            qcfg.weekly.model_dump(),
            qcfg.alert_threshold,
        )
        bcfg = settings.llm.budget
        cost_tracker.configure_budget(
            BudgetConfig(
                user_daily_limits_usd=dict(bcfg.user_daily_limits_usd),
                default_user_daily_limit_usd=bcfg.default_user_daily_limit_usd,
                global_daily_limit_usd=bcfg.global_daily_limit_usd,
                alert_threshold=bcfg.alert_threshold,
            )
        )
        logger.info(
            "LLM 预算配置就绪 global=%s default_user=%s explicit_users=%d alert=%.2f",
            bcfg.global_daily_limit_usd,
            bcfg.default_user_daily_limit_usd,
            len(bcfg.user_daily_limits_usd),
            bcfg.alert_threshold,
        )

        try:
            await cost_tracker.hydrate_baseline(db_module.get_session_factory())
        except Exception:  # noqa: BLE001
            logger.exception("CostTracker baseline hydrate 失败, 预算检查仍可用 (按内存累计)")
        try:
            await usage_quota.hydrate_baseline()
        except Exception:  # noqa: BLE001
            logger.exception("用户用量额度 baseline hydrate 失败, 仍可按内存累计")
    except Exception:  # noqa: BLE001
        logger.exception("CostTracker budget 配置失败, 预算检查降级为 no-op")

    # 4. RAG 组件 (软: 失败跳过, 除非 STRICT_RAG=true)
    _setup_rag_components(app, settings)

    logger.info("服务启动完成")

    try:
        yield
    finally:
        # ---------- 关闭 ----------
        logger.info("服务关闭中...")

        if hasattr(app.state, "llm_pool"):
            try:
                app.state.llm_pool.clear()
            except Exception as e:  # noqa: BLE001
                logger.warning("清理 llm_pool 失败: %s", e)

        # 兜底: 任何对象有 close/aclose 都调一下
        for attr in ("embedder", "vector_store", "bm25_store"):
            obj = getattr(app.state, attr, None)
            close = getattr(obj, "aclose", None) or getattr(obj, "close", None)
            if callable(close):
                try:
                    res = close()
                    if inspect.isawaitable(res):
                        await res
                except Exception as e:  # noqa: BLE001
                    logger.warning("关闭 %s 失败: %s", attr, e)

        try:
            await db_module.dispose_engine()
            logger.info("数据库已关闭")
        except Exception as e:  # noqa: BLE001
            logger.warning("关闭数据库失败: %s", e)

        logger.info("服务已停止")


def _setup_rag_components(app, settings) -> None:
    """装配 RAG 栈 (tokenizer / embedder / vector_store / bm25 / retriever).

    任一步骤失败:
        - STRICT_RAG=true → 重抛, 服务启动失败
        - 否则 → WARN 跳过, 该组件在 app.state 上不可见
    """
    failures: list[tuple[str, BaseException]] = []

    def _fail(name: str, exc: BaseException) -> None:
        """记录组件初始化失败; STRICT_RAG=true 时才打 ERROR, 否则 DEBUG 避免误报."""
        if STRICT_RAG:
            logger.exception("RAG 组件初始化失败: %s", name, exc_info=exc)
        else:
            logger.debug("RAG 组件初始化失败: %s — %s", name, exc)
        failures.append((name, exc))

    # 4a. Tokenizer
    tokenizer = None
    try:
        from forge.retrieval.common.tokenizer import TokenizerFactory

        tokenizer = TokenizerFactory.create(
            settings.bm25_store.tokenizer.type,
            settings.bm25_store.tokenizer.model_dump(exclude={"type"}),
        )
        app.state.tokenizer = tokenizer
        logger.info("Tokenizer 就绪: type=%s", settings.bm25_store.tokenizer.type)
    except Exception as e:  # noqa: BLE001
        _fail("tokenizer", e)

    # 4b. Embedder (走模型网关: 池化 + 可选 fallback chain)
    embedder = None
    try:
        from forge.retrieval.embedders.factory import (
            build_embedder_from_settings,
        )

        embedder = build_embedder_from_settings(settings)
        app.state.embedder = embedder
        logger.info(
            "Embedder 就绪: provider=%s fallbacks=%s",
            settings.embedding.provider,
            getattr(settings.embedding, "fallback_chain", []) or [],
        )
    except Exception as e:  # noqa: BLE001
        _fail("embedder", e)

    # 4c. Vector store
    vector_store = None
    try:
        from forge.retrieval.stores.vector.factory import VectorStoreFactory

        vector_store = VectorStoreFactory.create(
            settings.vector_store.provider,
            settings.vector_store.active_config(),
        )
        app.state.vector_store = vector_store
        logger.info("向量库就绪: %s", settings.vector_store.provider)
    except Exception as e:  # noqa: BLE001
        _fail("vector_store", e)

    # 4d. BM25 store
    bm25_store = None
    try:
        from forge.retrieval.stores.bm25.factory import BM25StoreFactory

        if tokenizer is None:
            raise RuntimeError("BM25 需要 tokenizer, 但 tokenizer 初始化已失败")
        bm25_store = BM25StoreFactory.create(
            settings.bm25_store.provider,
            settings.bm25_store.active_config(),
            tokenizer,
        )
        app.state.bm25_store = bm25_store
        logger.info("BM25 库就绪: %s", settings.bm25_store.provider)
    except Exception as e:  # noqa: BLE001
        _fail("bm25_store", e)

    # 4e. Retriever (依赖 vector_store / bm25_store / embedder)
    try:
        if embedder is None or vector_store is None or bm25_store is None:
            raise RuntimeError("retriever 需要 embedder / vector_store / bm25_store 全部就绪")
        from forge.retrieval.factory import RetrieverFactory

        retriever = RetrieverFactory.create(
            settings=settings,
            child_store=vector_store,
            bm25_store=bm25_store,
            embedder=embedder,
        )
        app.state.retriever = retriever
        # 同步发布到 retrieval 模块级单例, 供 knowledge_search 等工具读取
        from forge.retrieval.runtime import set_retriever

        set_retriever(retriever)
        logger.info("检索器就绪")
    except Exception as e:  # noqa: BLE001
        _fail("retriever", e)

    # 4f. 文件存储 + KbIngestService + KbService
    # 上传 API 需要这三个组件; 任一缺失则 KB 路由不可用 (路由内会报错).
    try:
        if embedder is None or vector_store is None or bm25_store is None:
            raise RuntimeError("KB ingest 需要 embedder / vector_store / bm25_store 全部就绪")

        from pathlib import Path

        from forge.api.services.kb_ingest_service import KbIngestService
        from forge.api.services.kb_service import KbService
        from forge.infrastructure.storage.local_fs import LocalFileStorage
        from forge.retrieval.chunkers import ChunkConfig
        from forge.retrieval.parsers.dispatcher import default_dispatcher

        upload_dir = Path(getattr(settings.ingest, "documents_path", None) or "data/documents")
        file_storage = LocalFileStorage(upload_dir / "uploads")
        chunk_cfg = ChunkConfig(
            child_target_chars=settings.ingest.chunking.chunk_size,
            child_overlap_chars=settings.ingest.chunking.chunk_overlap,
        )
        ingest_service = KbIngestService(
            child_store=vector_store,
            bm25_store=bm25_store,
            embedder=embedder,
            parser_dispatcher=default_dispatcher(),
            chunk_config=chunk_cfg,
        )
        kb_service = KbService(
            file_storage=file_storage,
            kb_ingest_service=ingest_service,
        )
        app.state.file_storage = file_storage
        app.state.kb_ingest_service = ingest_service
        app.state.kb_service = kb_service
        logger.info("KB 服务就绪: storage=%s", upload_dir / "uploads")
    except Exception as e:  # noqa: BLE001
        _fail("kb_service", e)

    if failures:
        # 完整 traceback 已经在 _fail() 里 logger.exception 打印过
        summary = ", ".join(f"{n}({type(e).__name__}: {e})" for n, e in failures)
        if STRICT_RAG:
            raise RuntimeError(f"STRICT_RAG=true, 下列 RAG 组件初始化失败: {summary}")
        logger.warning(
            "RAG 组件未就绪 (软降级): %s. "
            "如要让 /agent/chat 等不依赖 RAG 的接口照常工作可忽略; "
            "如要使用文档/检索功能, 装 extras: poetry install -E rag",
            summary,
        )
