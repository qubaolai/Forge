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
    业务侧通过 app.state.rag_runtime 判断可用性.
"""

from __future__ import annotations

import inspect
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from forge.config.settings import get_settings

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

    # 0.2 AgentProfile 加载 + 启动校验 (硬性: 任一项不通过 -> 阻止启动)
    #     依赖 ToolRegistry / PromptRegistry 都已就绪.
    import forge.tools  # noqa: F401  触发 builtin 工具注册
    from forge.agents.profiles import load_profiles_at_startup

    load_profiles_at_startup()

    # 1. Database (硬性)
    from forge.infrastructure.database import database as db_module

    db_module.init_engine()
    await db_module.ping()
    await db_module.bootstrap_schema()
    app.state.database = db_module
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
            facts_enabled=settings.memory.facts.enabled,
            extract_every_n_turns=settings.memory.facts.extract_every_n_turns,
        )
    except Exception:  # noqa: BLE001
        logger.exception("memory hooks 安装失败, 摘要/事实抽取任务不会被派发")

    # 2.3 context digest hooks (软: 失败仅日志; 与 memory 各自独立订阅 turn.completed)
    from forge.context_mgmt.digest.hooks import install_digest_hooks

    try:
        install_digest_hooks(
            event_bus,
            task_queue,
            enabled=settings.context.digest.enabled,
        )
    except Exception:  # noqa: BLE001
        logger.exception("digest hooks 安装失败, digest 任务不会被派发")

    # 2.4 语义召回 hooks (软: 失败仅日志; 默认关闭, 与 digest/memory 各自独立订阅)
    from forge.context_mgmt.recall.hooks import install_embedding_hooks

    try:
        install_embedding_hooks(
            event_bus,
            task_queue,
            enabled=settings.context.semantic_recall.enabled,
        )
    except Exception:  # noqa: BLE001
        logger.exception("语义召回 hooks 安装失败, embedding 任务不会被派发")

    # 3. Redis + ModelConfigCache: 从 DB 全量加载供应商和 Key 到 Redis
    from forge.infrastructure.cache.redis_client import RedisClient

    redis_client = RedisClient.from_settings()
    app.state.redis_client = redis_client

    from forge.llm.model_config_cache import ModelConfigCache

    model_cache = ModelConfigCache.get_global(redis_client)
    from forge.infrastructure.database.database import session_scope

    try:
        async with session_scope() as cache_db:
            await model_cache.reload_all(cache_db)
        logger.info("模型缓存加载完成: ready=%s", await model_cache.is_ready())
    except Exception:
        logger.exception("模型缓存加载失败, LLM 将不可用")

    providers = await model_cache.get_providers_enabled() if await model_cache.is_ready() else []
    logger.info("模型目录就绪（来源: 数据库缓存）: providers=%s", [p["name"] for p in providers] if providers else "[]")

    # 3.1 LLM 预热: 从 ModelConfigCache 读取启用的供应商和 Key
    # 硬要求: 配置中启用的 provider 必须能成功构建 client (代码/SDK 必须就绪);
    #         单个 Key 鉴权失败等运行时错误才是 "软失败".
    # 这里区分两类错误:
    #   - ValueError("未注册的 LLM provider") → 代码缺陷, 直接抛出阻止启动
    #   - 其他异常 (网络/Key 校验) → 计入 failed, 不阻塞启动
    from forge.llm.client_pool import get_llm_pool
    from forge.llm.registry import list_providers as _registered_providers

    llm_pool = get_llm_pool()
    warmed, failed = 0, 0
    fatal_errors: list[str] = []
    for p in providers:
        impl = p.get("impl") or p["name"]
        if impl not in _registered_providers():
            # provider 在 DB 启用但代码层未注册: 配置或代码错误, 必须阻止启动
            fatal_errors.append(
                f"provider={p['name']!r} impl={impl!r} 未在 LLM 注册表中, "
                f"已注册: {_registered_providers()}"
            )
            continue
        client_options = {"base_url": p.get("base_url"), "timeout": p.get("timeout", 30)}
        keys = await model_cache.get_keys(p["name"])
        for key_data in keys:
            api_key = key_data.get("api_key", "")
            if not api_key:
                continue
            try:
                llm_pool.warm(impl, api_key, client_options)
                llm_pool.register_key(impl, api_key, weight=key_data.get("weight", 1))
                warmed += 1
            except ValueError as e:
                # build_llm_client 抛 ValueError = 注册表缺失, 同样视为致命
                fatal_errors.append(
                    f"impl={impl!r} fingerprint={key_data.get('fingerprint', '?')} → {e}"
                )
                failed += 1
            except Exception:
                logger.exception(
                    "LLM 预热失败 (运行时错误): impl=%s fingerprint=%s",
                    impl, key_data.get("fingerprint", "?"),
                )
                failed += 1
    logger.info("LLM 池预热完成: 成功=%d 失败=%d 池容量=%d", warmed, failed, llm_pool.size())
    app.state.llm_pool = llm_pool

    if fatal_errors:
        # 致命: provider 在配置中启用却找不到实现, 服务必须停止
        msg = "LLM 预热致命错误, 服务无法启动:\n  - " + "\n  - ".join(fatal_errors)
        logger.error(msg)
        raise RuntimeError(msg)
    if warmed == 0 and providers:
        msg = (
            f"LLM 预热失败: 启用的 {len(providers)} 个 provider 中没有任何 Key 成功构建 client, "
            "无法对外提供 LLM 服务"
        )
        logger.error(msg)
        raise RuntimeError(msg)

    # 3.1.x LLMGateway 全局单例初始化 (业务层唯一对外入口)
    from forge.llm.gateway import get_llm_gateway, reset_llm_gateway

    reset_llm_gateway()  # 兼容 reload (uvicorn --reload)
    llm_gateway = get_llm_gateway(settings, model_cache=model_cache)
    app.state.llm_gateway = llm_gateway
    logger.info("LLMGateway 已就绪")

    # 3.2 CostTracker: 加载 budget 配置 + baseline hydrate
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
            await cost_tracker.hydrate_baseline()
        except Exception:  # noqa: BLE001
            logger.exception("CostTracker baseline hydrate 失败, 预算检查仍可用 (按内存累计)")
        try:
            await usage_quota.hydrate_baseline()
        except Exception:  # noqa: BLE001
            logger.exception("用户用量额度 baseline hydrate 失败, 仍可按内存累计")
    except Exception:  # noqa: BLE001
        logger.exception("CostTracker budget 配置失败, 预算检查降级为 no-op")

    app.state.model_cache = model_cache

    # 3.3 LLM 网关 Phase 4/6: 入站限流 + 舱壁 + Redis-backed 实例注入
    try:
        await _setup_llm_gateway_runtime(settings, redis_client)
    except Exception:  # noqa: BLE001
        logger.exception("LLM 网关运行时配置失败, 降级使用进程内默认实现")

    # 4. RAG 组件 (软: 失败跳过, 除非 STRICT_RAG=true)
    await _setup_rag_components(app, settings, model_cache)

    # 5. ChatTurnSupervisor 后台清理与进程关闭收尾
    from forge.chat.supervisor import get_chat_supervisor

    chat_supervisor = get_chat_supervisor()
    chat_supervisor.start_cleanup_loop()
    app.state.chat_supervisor = chat_supervisor

    logger.info("服务启动完成")

    try:
        yield
    finally:
        # ---------- 关闭 ----------
        logger.info("服务关闭中...")

        # ChatTurnSupervisor 收尾: 通知仍在生成的对话中断并落库
        try:
            await get_chat_supervisor().shutdown()
        except Exception:  # noqa: BLE001
            logger.exception("ChatTurnSupervisor 关闭失败")

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


async def _setup_llm_gateway_runtime(settings, redis_client) -> None:
    """LLM 网关运行时配置: 入站限流 + 舱壁 + (Redis 可用时) Redis-backed 实现.

    设计:
        - 默认进程内实现 (零外部依赖, 单机可用)
        - Redis 连通时自动升级到 Redis-backed 实例 (跨实例共享状态)
        - Redis 不可用时静默保持进程内, 不抛异常
    """
    # 入站限流
    rl_cfg = settings.llm.inbound_rate_limit
    use_redis = redis_client is not None and await redis_client.ping()
    if rl_cfg.enabled:
        from forge.llm.inbound_rate_limiter import (
            InboundRateLimiter,
            InProcessInboundRateLimiter,
            RedisInboundRateLimiter,
            set_inbound_rate_limiter,
        )
        limiter: InboundRateLimiter
        if use_redis:
            limiter = RedisInboundRateLimiter(
                redis_client,
                enabled=True,
                rpm=rl_cfg.rpm,
                tpm=rl_cfg.tpm,
                window_seconds=rl_cfg.window_seconds,
            )
            logger.info(
                "LLM 入站限流就绪 (Redis): rpm=%s tpm=%s window=%.0fs",
                rl_cfg.rpm, rl_cfg.tpm, rl_cfg.window_seconds,
            )
        else:
            limiter = InProcessInboundRateLimiter(
                enabled=True,
                rpm=rl_cfg.rpm,
                tpm=rl_cfg.tpm,
                window_seconds=rl_cfg.window_seconds,
            )
            logger.info(
                "LLM 入站限流就绪 (进程内): rpm=%s tpm=%s window=%.0fs",
                rl_cfg.rpm, rl_cfg.tpm, rl_cfg.window_seconds,
            )
        set_inbound_rate_limiter(limiter)

    # 舱壁: 进程内即可 (per-provider asyncio.Semaphore, 不需要跨实例)
    from forge.llm.resilience.bulkhead import ProviderBulkhead, set_bulkhead
    set_bulkhead(ProviderBulkhead(
        max_concurrent_per_provider=settings.llm.bulkhead_max_concurrent
    ))
    logger.info(
        "LLM 舱壁就绪: max_concurrent_per_provider=%d",
        settings.llm.bulkhead_max_concurrent,
    )

    # Redis-backed 幂等 + 精确缓存 (Redis 可用时升级)
    if use_redis:
        from forge.llm.caching.exact_cache import RedisExactCache, set_exact_cache
        from forge.llm.pipeline.dedup import RedisIdempotencyStore, set_idempotency_store

        set_exact_cache(RedisExactCache(redis_client))
        set_idempotency_store(RedisIdempotencyStore(redis_client=redis_client))
        logger.info("LLM 精确缓存 + 幂等存储已升级到 Redis backend")
    else:
        logger.info("Redis 不可用: LLM 精确缓存 / 幂等存储保持进程内 (单机模式)")


async def _setup_rag_components(app, settings, model_cache=None) -> None:
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

    # 4b. BM25 store
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

    # 4c. RAG runtime: Embedding/Reranker 仅按 DB 系统绑定动态解析
    rag_runtime = None
    try:
        if bm25_store is None:
            raise RuntimeError("RAG runtime 需要 BM25 store")
        from forge.retrieval.rag_runtime import RagRuntime, set_rag_runtime

        rag_runtime = RagRuntime(settings=settings, bm25_store=bm25_store)
        set_rag_runtime(rag_runtime)
        app.state.rag_runtime = rag_runtime
        logger.info("RAG runtime 就绪，Embedding/Reranker 将从 DB 系统绑定解析")
    except Exception as e:  # noqa: BLE001
        _fail("rag_runtime", e)

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
