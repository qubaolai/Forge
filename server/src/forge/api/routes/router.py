"""路由聚合。

所有版本路由在这里挂载。新增 v2 时新增 v2 子目录 include 即可。
"""

from __future__ import annotations

from fastapi import APIRouter

from forge.api.routes.v1 import (
    admin,
    api_keys,
    auth,
    chat,
    llm,
    models,
    providers,
    sessions,
    system,
    users,
)

api_router = APIRouter()

v1 = APIRouter(prefix="/v1")
v1.include_router(system.router, tags=["system"])
v1.include_router(auth.router, prefix="/auth", tags=["auth"])
v1.include_router(sessions.router)
v1.include_router(chat.router, prefix="/chat", tags=["chat"])
v1.include_router(llm.router)
v1.include_router(users.router)
v1.include_router(api_keys.router)
v1.include_router(models.router, tags=["models"])
v1.include_router(providers.router, tags=["providers"])
v1.include_router(admin.router)


api_router.include_router(v1)
