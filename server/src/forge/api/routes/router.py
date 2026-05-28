"""路由聚合。

所有版本路由在这里挂载。新增 v2 时新增 v2 子目录 include 即可。
"""

from __future__ import annotations

from fastapi import APIRouter

from forge.api.dependencies import AuthenticatedUser
from forge.api.routes.v1 import (
    admin,
    api_keys,
    artifacts,
    auth,
    chat,
    decisions,
    knowledge_bases,
    models,
    providers,
    runs,
    sessions,
    system,
    users,
)
from forge.core.response import success
from forge.tools.registry import ToolRegistry

api_router = APIRouter()

v1 = APIRouter(prefix="/v1")
v1.include_router(system.router, tags=["system"])
v1.include_router(auth.router, prefix="/auth", tags=["auth"])
v1.include_router(sessions.router)
v1.include_router(chat.router, prefix="/chat", tags=["chat"])
v1.include_router(runs.router)
v1.include_router(artifacts.router)
v1.include_router(users.router)
v1.include_router(api_keys.router)
v1.include_router(knowledge_bases.router)
v1.include_router(models.router, tags=["models"])
v1.include_router(providers.router, tags=["providers"])
v1.include_router(admin.router)
v1.include_router(decisions.router)


@v1.get("/tools", tags=["tools"])
async def list_tools(user: AuthenticatedUser):
    """列出已注册工具。"""
    _ = user
    items = [
        {
            "id": t.name,
            "name": t.name,
            "description": t.description,
            "category": "general",
            "parameters_schema": t.parameters,
            "is_dangerous": False,
        }
        for t in ToolRegistry.get_all()
    ]
    return success(items)


api_router.include_router(v1)
