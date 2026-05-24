"""供应商路由 — 公开查询 + 管理端操作。

公开接口（读 Redis 缓存，无需登录）:
    GET  /providers              列出已启用供应商（供 ChatInput 下拉）

管理接口（需 AdminUser）:
    GET  /providers/admin        全量供应商列表（含模型详情、Key 数量）
    PUT  /providers/{name}       启用/禁用供应商
"""

from fastapi import APIRouter, Depends

from forge.api.dependencies import AdminUser, DbSession, get_model_cache
from forge.api.schemas.admin import ProviderToggleIn
from forge.core.exceptions import NotFound
from forge.core.response import success

router = APIRouter()


# ==================================================================
# 公开接口 — 读 Redis 缓存
# ==================================================================
@router.get("/providers")
async def list_providers(db: DbSession, model_cache=Depends(get_model_cache)):
    """列出已启用的供应商（名称列表，供 ChatInput 下拉）。"""
    from forge.llm.model_config_cache import ModelConfigCache

    cache = model_cache if isinstance(model_cache, ModelConfigCache) else ModelConfigCache.get_global()
    if await cache.is_ready():
        providers = await cache.get_providers_enabled()
        return success([
            {"name": p["name"], "impl": p.get("impl", p["name"])}
            for p in providers
        ])
    # Redis 不可用时降级读 DB
    from forge.infrastructure.database.repositories.model_provider_repo import ProviderRepository
    repo = ProviderRepository(db)
    providers = await repo.list_enabled()
    return success([
        {"name": p.name, "impl": p.impl or p.name}
        for p in providers
    ])


# ==================================================================
# 管理接口 — 需 AdminUser
# ==================================================================
@router.get("/providers/admin")
async def list_providers_admin(admin: AdminUser, db: DbSession, model_cache=Depends(get_model_cache)):
    """列出所有供应商及其模型详情（管理端完整视图）。"""
    from forge.api.services.admin_model_service import AdminModelService
    from forge.llm.model_config_cache import ModelConfigCache

    cache = model_cache if isinstance(model_cache, ModelConfigCache) else ModelConfigCache.get_global()
    svc = AdminModelService(db, cache)
    providers = await svc.list_providers()
    return success(providers)


@router.put("/providers/{provider_name}")
async def toggle_provider(
    provider_name: str, body: ProviderToggleIn, admin: AdminUser, db: DbSession, model_cache=Depends(get_model_cache)
):
    """启用/禁用供应商。"""
    from forge.api.services.admin_model_service import AdminModelService
    from forge.llm.model_config_cache import ModelConfigCache

    cache = model_cache if isinstance(model_cache, ModelConfigCache) else ModelConfigCache.get_global()
    svc = AdminModelService(db, cache)
    try:
        result = await svc.toggle_provider(provider_name, body.enabled)
    except ValueError as e:
        raise NotFound(str(e), code=40460)
    return success(result)
