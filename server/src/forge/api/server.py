"""FastAPI 应用入口.

职责:
- 创建 FastAPI 实例
- 注册 lifespan (启动/关闭钩子)
- 注册中间件 (CORS / tracing / error_handler)
- 聚合路由

中间件执行顺序 (后注册先执行):
    error_handler → tracing → client_type → CORS → 路由

单机化改造后: 砍掉 RateLimit / Auth 中间件 (无 Redis 依赖, 无多用户体系).
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from forge.api.lifespan import lifespan
from forge.api.middleware.client_type import ClientTypeMiddleware
from forge.api.middleware.error_handler import ErrorHandlerMiddleware
from forge.api.middleware.tracing import TracingMiddleware
from forge.api.routes.router import api_router
from forge.config.settings import get_settings
from forge.core.exceptions import register_exception_handlers


def create_app() -> FastAPI:
    settings = get_settings()  # fail-fast: 校验失败立刻抛

    app = FastAPI(
        title="Agent Platform",
        version="0.1.0",
        description="单机版 Agent 副驾后端 (RAG + Agent + Tool calling)",
        lifespan=lifespan,
    )

    # ── CORS ──────────────────────────────────────────────────────────
    cors_origins = settings.middleware.cors.origins_list()
    allow_credentials = settings.middleware.cors.allow_credentials
    # 浏览器规范: allow_origins=["*"] 与 allow_credentials=True 不兼容,
    # 用 True 时必须列具体 origin. 这里加保护防止误配.
    if cors_origins == ["*"] and allow_credentials:
        allow_credentials = False
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Client type (S6.5 M4: 只透传元数据, local 模式不收紧策略) ─────
    app.add_middleware(ClientTypeMiddleware)

    # ── Tracing (给所有后续日志加 trace_id) ───────────────────────────
    app.add_middleware(TracingMiddleware)

    # ── 未捕获异常兜底 (最后注册 = 最先执行 = 最外层) ─────────────────
    app.add_middleware(ErrorHandlerMiddleware)

    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api")

    _try_mount_metrics(app)

    return app


def _try_mount_metrics(app) -> None:
    """挂 Prometheus /metrics 端点 (软依赖).

    无 prometheus_client 时跳过, 日志一行 INFO. 装了就在根路径暴露
    asgi 子应用, 与 /api 业务路径并列.
    """
    try:
        from prometheus_client import make_asgi_app
    except ImportError:
        import logging

        logging.getLogger(__name__).info(
            "/metrics 端点未启用 (prometheus_client 未安装, poetry install -E metrics 启用)"
        )
        return
    app.mount("/metrics", make_asgi_app())


app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "forge.api.server:app",
        host="127.0.0.1",
        port=settings.app.port,
        reload=False,
        log_level=settings.app.log_level.lower(),
    )
