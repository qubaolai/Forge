"""供应商路由 — 公开查询 + 管理端操作。

公开接口（读 DB，无需登录）:
    GET  /providers              列出所有供应商（含启停状态）

管理接口（需 AdminUser）:
    GET    /providers/admin                      全量供应商列表（含模型详情、Key 数量）
    PUT    /providers/{name}                     启用/禁用供应商
    POST   /providers/{name}/models             新增模型
    GET    /providers/{name}/keys               列出供应商 API-Key（脱敏）
    POST   /providers/{name}/keys               新增 API-Key
    PUT    /providers/{name}/keys/{key_id}      更新 API-Key（启停/权重）
    DELETE /providers/{name}/keys/{key_id}      删除 API-Key
"""

from fastapi import APIRouter, Depends

from forge.api.dependencies import AdminUser, DbSession, get_model_cache
from forge.api.schemas.admin import (
    ModelCreateIn,
    ProviderKeyCreateIn,
    ProviderKeyUpdateIn,
    ProviderToggleIn,
)
from forge.core.exceptions import NotFound
from forge.core.response import success


def _admin_service(db, model_cache):
    """构造 AdminModelService（统一解析 model_cache）。"""
    from forge.api.services.admin_model_service import AdminModelService
    from forge.llm.model_config_cache import ModelConfigCache

    cache = model_cache if isinstance(model_cache, ModelConfigCache) else ModelConfigCache.get_global()
    return AdminModelService(db, cache)


router = APIRouter()


# ==================================================================
# 公开接口 — 读 Redis 缓存
# ==================================================================
@router.get("/providers")
async def list_providers(db: DbSession, model_cache=Depends(get_model_cache)):
    """列出所有供应商（不按 is_enabled 过滤）。"""
    from forge.infrastructure.database.repositories.model_provider_repo import ProviderRepository
    repo = ProviderRepository(db)
    providers = await repo.list_all()
    return success([
        {
            "id": str(p.id),
            "name": p.name,
            "impl": p.impl or p.name,
            "is_enabled": bool(p.is_enabled),
        }
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
    svc = _admin_service(db, model_cache)
    try:
        result = await svc.toggle_provider(provider_name, body.enabled)
    except ValueError as e:
        raise NotFound(str(e), code=40460)
    return success(result)


# ==================================================================
# 模型 — 在供应商下新增
# ==================================================================
@router.post("/providers/{provider_name}/models")
async def create_model(
    provider_name: str, body: ModelCreateIn, admin: AdminUser, db: DbSession,
    model_cache=Depends(get_model_cache),
):
    """在指定供应商下新增模型。"""
    svc = _admin_service(db, model_cache)
    try:
        result = await svc.create_model(provider_name, body.model_dump())
    except ValueError as e:
        raise NotFound(str(e), code=40460) from e
    return success(result)


# ==================================================================
# 供应商 API-Key — 增删改查
# ==================================================================
@router.get("/providers/{provider_name}/keys")
async def list_provider_keys(
    provider_name: str, admin: AdminUser, db: DbSession, model_cache=Depends(get_model_cache),
):
    """列出供应商的 API-Key（脱敏，绝不返回明文）。"""
    svc = _admin_service(db, model_cache)
    try:
        result = await svc.list_provider_keys(provider_name)
    except ValueError as e:
        raise NotFound(str(e), code=40460) from e
    return success(result)


@router.post("/providers/{provider_name}/keys")
async def create_provider_key(
    provider_name: str, body: ProviderKeyCreateIn, admin: AdminUser, db: DbSession,
    model_cache=Depends(get_model_cache),
):
    """新增一条 API-Key（明文输入，后端加密落库）。"""
    svc = _admin_service(db, model_cache)
    try:
        result = await svc.create_provider_key(provider_name, body.api_key, body.weight)
    except ValueError as e:
        raise NotFound(str(e), code=40460) from e
    return success(result)


@router.put("/providers/{provider_name}/keys/{key_id}")
async def update_provider_key(
    provider_name: str, key_id: str, body: ProviderKeyUpdateIn, admin: AdminUser, db: DbSession,
    model_cache=Depends(get_model_cache),
):
    """更新 API-Key 的启停 / 权重。"""
    svc = _admin_service(db, model_cache)
    try:
        result = await svc.update_provider_key(
            provider_name, key_id, enabled=body.enabled, weight=body.weight
        )
    except ValueError as e:
        raise NotFound(str(e), code=40460) from e
    return success(result)


@router.delete("/providers/{provider_name}/keys/{key_id}")
async def delete_provider_key(
    provider_name: str, key_id: str, admin: AdminUser, db: DbSession,
    model_cache=Depends(get_model_cache),
):
    """删除一条 API-Key。"""
    svc = _admin_service(db, model_cache)
    try:
        result = await svc.delete_provider_key(provider_name, key_id)
    except ValueError as e:
        raise NotFound(str(e), code=40460) from e
    return success(result)
