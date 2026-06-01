"""模型路由 — 公开查询 + 管理端操作。

公开接口（读 Redis 缓存，无需登录）:
    GET  /models              列出所有已启用模型（按供应商分组）
    GET  /models?provider=xxx 按供应商筛选

管理接口（需 AdminUser）:
    GET  /models/{id}         按数据库 id 查询模型详情
    PUT  /models/{model_id}   切换启用状态  {enabled: true/false}
    POST /models/{model_id}/set-default  设为默认模型
"""

from fastapi import APIRouter, Depends, Query

from forge.api.dependencies import AdminUser, DbSession, get_model_cache
from forge.api.schemas.admin import ModelUpdateIn
from forge.core.exceptions import NotFound
from forge.core.response import success

router = APIRouter()


def _build_thinking_meta(model: dict) -> dict | None:
    """组装前端可直接消费的 thinking 配置。"""
    if not model.get("supports_thinking"):
        return None

    options = model.get("thinking_options") or None
    default = options[0] if options else None
    return {"options": options, "default": default}


def _to_model_info(model: dict, provider_name: str) -> dict:
    """统一模型输出结构（供 ChatInput 使用）。"""
    return {
        "provider": provider_name,
        "model_id": model.get("model_id", ""),
        "name": model.get("name", ""),
        "display_name": model.get("display_name", ""),
        "model_type": model.get("model_type", "text"),
        "context_window": int(model.get("context_window") or 0),
        "supports_tools": bool(model.get("supports_tools", False)),
        "supports_images": bool(model.get("supports_images", False)),
        "supports_thinking": bool(model.get("supports_thinking", False)),
        "thinking": _build_thinking_meta(model),
    }


# ==================================================================
# 公开接口 — 读 Redis 缓存
# ==================================================================
@router.get("/models")
async def list_models(
    db: DbSession,
    model_cache=Depends(get_model_cache),
    provider: str | None = Query(None, description="按供应商名称筛选"),
    model_type: str | None = Query(None, description="按类型筛选: text/embedding/reranker"),
):
    """列出可用模型（按供应商分组）。"""
    from forge.infrastructure.database.repositories.model_provider_repo import ProviderRepository
    from forge.infrastructure.database.repositories.model_repo import ModelRepository
    from forge.llm.model_config_cache import ModelConfigCache

    cache = model_cache if model_cache is not None else ModelConfigCache.get_global()
    groups: list[dict] = []

    if await cache.is_ready():
        providers = await cache.get_providers_enabled()
        for provider_item in providers:
            provider_name = provider_item.get("name", "")
            if not provider_name:
                continue
            if provider and provider_name != provider:
                continue

            provider_models = await cache.get_models(provider_name, enabled_only=True)
            if model_type:
                provider_models = [m for m in provider_models if m.get("model_type") == model_type]
            if not provider_models:
                continue

            models = [_to_model_info(m, provider_name) for m in provider_models]
            groups.append({"provider": provider_name, "models": models})
    else:
        # Redis 不可用时降级读 DB
        provider_repo = ProviderRepository(db)
        model_repo = ModelRepository(db)
        provider_rows = await provider_repo.list_enabled()
        if provider:
            provider_rows = [p for p in provider_rows if p.name == provider]

        for provider_row in provider_rows:
            provider_models = await model_repo.list_by_provider(provider_row.id, enabled_only=True)
            if model_type:
                provider_models = [m for m in provider_models if m.model_type == model_type]
            if not provider_models:
                continue

            models = [
                _to_model_info(
                    {
                        "model_id": m.model_id,
                        "name": m.name,
                        "display_name": m.display_name,
                        "model_type": m.model_type,
                        "context_window": m.context_window,
                        "supports_tools": m.supports_tools,
                        "supports_images": m.supports_images,
                        "supports_thinking": m.supports_thinking,
                        "thinking_options": m.thinking_options,
                    },
                    provider_row.name,
                )
                for m in provider_models
            ]
            groups.append({"provider": provider_row.name, "models": models})

        # 兼容极端分支：provider 过滤且 provider 不存在，返回空分组
        if provider and not groups:
            prov = await provider_repo.get_by_name(provider)
            if prov and not prov.is_enabled:
                groups = []

    flat_models = [m for g in groups for m in g["models"]]
    providers = [g["provider"] for g in groups]
    payload: dict = {
        "groups": groups,
        # 兼容历史前端字段
        "providers": providers,
        "models": flat_models,
    }
    if provider:
        payload["provider"] = provider
    if model_type:
        payload["model_type"] = model_type
    return success(payload)


# ==================================================================
# 管理接口 — 需 AdminUser
# ==================================================================
@router.get("/models/{id}")
async def get_model_detail(
    id: int,
    admin: AdminUser,
    db: DbSession,
    model_cache=Depends(get_model_cache),
):
    """按数据库 id 查询模型详情（用于编辑前拉取最新配置）。"""
    from forge.api.services.admin_model_service import AdminModelService
    from forge.llm.model_config_cache import ModelConfigCache

    cache = model_cache if isinstance(model_cache, ModelConfigCache) else ModelConfigCache.get_global()
    svc = AdminModelService(db, cache)
    try:
        result = await svc.get_model_by_id(id)
    except ValueError as e:
        raise NotFound(str(e), code=40461) from e
    return success(result)


@router.put("/models/{model_id}")
async def update_model(
    model_id: str,
    body: ModelUpdateIn,
    admin: AdminUser,
    db: DbSession,
    model_cache=Depends(get_model_cache),
):
    """更新模型配置（全字段：启停 / 设默认 / 上下文窗口 / 能力 / 优先级等）。"""
    from forge.api.services.admin_model_service import AdminModelService
    from forge.llm.model_config_cache import ModelConfigCache

    cache = model_cache if isinstance(model_cache, ModelConfigCache) else ModelConfigCache.get_global()
    svc = AdminModelService(db, cache)
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise NotFound("更新内容为空", code=40061)
    try:
        result = await svc.update_model_fields(model_id, data)
    except ValueError as e:
        raise NotFound(str(e), code=40461) from e
    return success(result)


@router.delete("/models/{model_id}")
async def delete_model(
    model_id: str,
    admin: AdminUser,
    db: DbSession,
    model_cache=Depends(get_model_cache),
):
    """删除模型。"""
    from forge.api.services.admin_model_service import AdminModelService
    from forge.llm.model_config_cache import ModelConfigCache

    cache = model_cache if isinstance(model_cache, ModelConfigCache) else ModelConfigCache.get_global()
    svc = AdminModelService(db, cache)
    try:
        result = await svc.delete_model(model_id)
    except ValueError as e:
        raise NotFound(str(e), code=40461) from e
    return success(result)
