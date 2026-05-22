"""路由聚合.

所有版本路由在这里挂载. 新增 v2 时, 新增 v2 子目录然后在这里 include 即可,
不影响 v1.

单机化改造后保留 auth / users (CLI / Web 都需登录); 砍掉:
    - audit  路由: 改读 ``<global>/audit.jsonl``, 命令行 ``jq`` / ``grep`` 即可
    - models 路由: 并入 ``config.yaml``, 非运行时可变

N20 评估结论 (2026-05-22):
    ``/agents`` 路由前端 (web/src/api/index.ts agentsApi) 在用于
    "对话 Agent persona CRUD"，与新 Forge 的"动态规划/多 Agent 执行层"
    无冲突——前者是用户级配置，后者是 Planner 内部原语。
    因此保留 ``/agents`` 注册，仅在文档层面明确定位；不再用于内部 agent
    管理 (Forge planner 不读取 AgentOrm)。
"""

from __future__ import annotations

from fastapi import APIRouter

from forge.api.dependencies import CurrentUser
from forge.api.routes.v1 import (
    agents,
    artifacts,
    auth,
    chat,
    knowledge_bases,
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
v1.include_router(agents.router)
v1.include_router(chat.router, prefix="/chat", tags=["chat"])
v1.include_router(runs.router)
v1.include_router(artifacts.router)
v1.include_router(users.router)
v1.include_router(knowledge_bases.router)


@v1.get("/tools", tags=["tools"])
async def list_tools(user: CurrentUser):
    """列出已注册工具 (前端 toolsApi.list 调用此路径)."""
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
