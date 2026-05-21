"""系统类接口:健康检查、就绪检查。

约定:
- /health 用于探活,极简,不做依赖检查
- /ready  用于就绪检测,会检查关键依赖是否可用
"""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    """k8s/负载均衡探活用,不做重操作。

    不走 ApiResponse 包装,纯字符串体。前端不调这个接口。
    """
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> dict:
    """检查关键依赖是否就绪。"""
    state = request.app.state
    checks = {
        "database": hasattr(state, "database"),
        "embedder": hasattr(state, "embedder"),
        "vector_store": hasattr(state, "vector_store"),
        "bm25_store": hasattr(state, "bm25_store"),
        "retriever": hasattr(state, "retriever"),
        "llm_pool": hasattr(state, "llm_pool"),
    }
    return {
        "status": "ok" if all(checks.values()) else "degraded",
        "checks": checks,
    }
