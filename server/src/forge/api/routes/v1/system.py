"""系统类接口: 健康检查、就绪检查、可用模型列表。"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from forge.core.response import success
from forge.llm.model_catalog import get_model_catalog

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


@router.get("/models")
async def list_models(provider: str = Query("")):
    """列出可用模型。不传 provider 返回所有 provider 列表。"""
    catalog = get_model_catalog()
    if provider:
        models = catalog.get_models(provider)
        return success({
            "models": [
                {
                    "name": m.name, "provider": m.provider,
                    "display_name": m.display_name, "context_window": m.context_window,
                    "supports_tools": m.supports_tools, "supports_images": m.supports_images,
                    "thinking": {
                        "type": m.thinking.type,
                        "options": m.thinking.options,
                        "default": m.thinking.default,
                    } if m.thinking else None,
                }
                for m in models
            ],
            "provider": provider,
        })
    return success({"providers": catalog.list_providers()})
