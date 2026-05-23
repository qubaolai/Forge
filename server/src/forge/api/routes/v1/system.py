"""系统类接口: 健康检查、就绪检查。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from forge.core.response import success

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> dict:
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
