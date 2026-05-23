"""模型路由 — 公开查询 + 管理端操作。

公开接口（读 Redis 缓存，无需登录）:
    GET  /models              列出所有已启用模型（供 ChatInput 下拉）
    GET  /models?provider=xxx 按供应商筛选

管理接口（需 AdminUser）:
    PUT  /models/{model_id}   切换启用状态  {enabled: true/false}
    POST /models/{model_id}/set-default  设为默认模型
"""

from fastapi import APIRouter, Depends, Query

from forge.api.dependencies import AdminUser, DbSession, get_model_cache
from forge.api.schemas.admin import ModelOut
from forge.core.exceptions import NotFound
from forge.core.response import success

router = APIRouter()


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
    """列出可用模型。从 Redis 缓存读取，O(1)。"""
    from forge.llm.model_config_cache import ModelConfigCache

    cache = model_cache if isinstance(model_cache, ModelConfigCache) else ModelConfigCache.get_global()
    if not await cache.is_ready():
        # Redis 不可用时降级读 DB
        from forge.infrastructure.database.repositories.model_repo import ModelRepository
        repo = ModelRepository(db)
        if model_type:
            models = await repo.list_enabled_by_type(model_type)
        else:
            from forge.infrastructure.database.orm.model_orm import ModelOrm
            from sqlalchemy import select
            stmt = select(ModelOrm).where(ModelOrm.is_enabled == True).order_by(ModelOrm.priority.desc())  # noqa: E712
            if provider:
                from forge.infrastructure.database.repositories.model_provider_repo import ProviderRepository
                prov = await ProviderRepository(db).get_by_name(provider)
                if prov:
                    stmt = stmt.where(ModelOrm.provider_id == prov.id)
            res = await db.execute(stmt)
            models = list(res.scalars().all())
        return success([
            ModelOut(
                model_id=m.model_id, name=m.name, display_name=m.display_name,
                model_type=m.model_type, is_enabled=m.is_enabled,
                is_default=m.is_default, priority=m.priority, cost_tier=m.cost_tier,
            ) for m in models
        ])

    if provider:
        models = await cache.get_models(provider, enabled_only=True)
        return success([
            ModelOut(
                model_id=m.get("model_id",""), name=m["name"], display_name=m.get("display_name",""),
                model_type=m.get("model_type","text"), is_enabled=m.get("is_enabled",True),
                is_default=m.get("is_default",False), priority=m.get("priority",0),
                cost_tier=m.get("cost_tier","mid"),
            ) for m in models
        ])

    if model_type:
        models = await cache.get_models_by_type(model_type)
        return success([
            ModelOut(
                model_id=m.get("model_id",""), name=m["name"], display_name=m.get("display_name",""),
                model_type=m.get("model_type","text"), is_enabled=m.get("is_enabled",True),
                is_default=m.get("is_default",False), priority=m.get("priority",0),
                cost_tier=m.get("cost_tier","mid"),
            ) for m in models
        ])

    # 返回所有已启用模型
    providers = await cache.get_providers_enabled()
    all_models: list[dict] = []
    for p in providers:
        all_models.extend(await cache.get_models(p["name"], enabled_only=True))
    return success([
        ModelOut(
            model_id=m.get("model_id",""), name=m["name"], display_name=m.get("display_name",""),
            model_type=m.get("model_type","text"), is_enabled=m.get("is_enabled",True),
            is_default=m.get("is_default",False), priority=m.get("priority",0),
            cost_tier=m.get("cost_tier","mid"),
        ) for m in all_models
    ])


# ==================================================================
# 管理接口 — 需 AdminUser
# ==================================================================
@router.put("/models/{model_id}")
async def update_model(
    model_id: str,
    body: dict,  # {enabled: bool, is_default: bool, priority: int, ...}
    admin: AdminUser,
    db: DbSession,
    model_cache=Depends(get_model_cache),
):
    """更新模型配置（启用/禁用/设默认/优先级）。"""
    from forge.api.services.admin_model_service import AdminModelService
    from forge.llm.model_config_cache import ModelConfigCache

    cache = model_cache if isinstance(model_cache, ModelConfigCache) else ModelConfigCache.get_global()
    svc = AdminModelService(db, cache)

    if "enabled" in body:
        try:
            result = await svc.toggle_model(model_id, body["enabled"])
        except ValueError as e:
            raise NotFound(str(e), code=40461)
        return success(result)

    if body.get("is_default"):
        try:
            result = await svc.set_default_model(model_id)
        except ValueError as e:
            raise NotFound(str(e), code=40461)
        return success(result)

    raise NotFound(f"不支持的更新字段: {list(body.keys())}", code=40061)
