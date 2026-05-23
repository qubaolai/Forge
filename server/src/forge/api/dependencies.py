"""依赖注入。

认证模式 (两套兼容):
    - ``AuthenticatedUser`` (JWT, 用于 Web / 多端登录场景)
        客户端从 ``/auth/login`` 拿 access_token, 后续 ``Authorization: Bearer ...``
        进程内 ``UserCache`` (TTL) 减轻 DB 压力.
    - ``ApiKeyUser`` (API Key, 用于 CLI / 第三方工具分机部署)
        客户端在 Web UI 或 CLI 创建 API Key 后, 请求带 ``X-API-Key`` header.
        Key 的 SHA256 哈希存库, 明文仅在创建时返回一次.

两者并存: 路由按场景选其中一个.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from forge.core.exceptions import Unauthorized
from forge.core.security import get_token_payload
from forge.infrastructure.database.database import get_db
from forge.infrastructure.database.orm.user_orm import UserOrm

# ---------- 数据库 ----------
DbSession = Annotated[AsyncSession, Depends(get_db)]


# ---------- JWT 当前用户 (Web 登录 / 多端) ----------
async def _get_jwt_current_user(
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> UserOrm:
    """从 Authorization header 解析当前用户 (走进程内 UserCache)."""
    if not authorization:
        raise Unauthorized("缺少 Authorization 头", code=40104)

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise Unauthorized("Authorization 头格式错误", code=40105)

    payload = get_token_payload(parts[1], expected_type="access")
    user_id = payload.get("sub")
    if not user_id:
        raise Unauthorized("token 缺少 sub 字段", code=40106)

    # 延迟 import 避免循环
    from forge.infrastructure.database.repositories.user_repo import UserRepository
    from forge.infrastructure.database.user_cache import UserCache

    user = await UserCache(UserRepository(db)).get(user_id)
    if not user:
        raise Unauthorized("用户不存在", code=40107)
    if user.status != "active":
        raise Unauthorized("用户已被禁用", code=40108)
    return user


AuthenticatedUser = Annotated[UserOrm, Depends(_get_jwt_current_user)]


# ---------- 管理员 (owner / admin 角色) ----------
async def _require_admin(user: AuthenticatedUser) -> UserOrm:
    from forge.core.exceptions import Forbidden

    if user.role not in ("owner", "admin"):
        raise Forbidden("仅管理员可访问", code=40311)
    return user


AdminUser = Annotated[UserOrm, Depends(_require_admin)]


# ---------- API Key 认证 (CLI / 第三方工具分机部署) ----------
async def _get_api_key_user(
    db: DbSession,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> UserOrm:
    """从 X-API-Key header 解析并验证 API Key，返回关联用户。

    Key 在库中存 SHA256 哈希，明文仅在创建时返回一次。
    user_id 为 BIGINT，需二次查询用户表。
    """
    from datetime import UTC, datetime

    from forge.core.exceptions import Unauthorized
    from forge.core.security import hash_api_key
    from forge.infrastructure.database.repositories.api_key_repo import (
        ApiKeyRepository,
    )
    from forge.infrastructure.database.repositories.user_repo import (
        UserRepository,
    )

    if not x_api_key:
        raise Unauthorized("缺少 X-API-Key 头", code=40130)

    key_hash = hash_api_key(x_api_key)
    key_repo = ApiKeyRepository(db)
    api_key = await key_repo.get_by_hash(key_hash)

    if not api_key:
        raise Unauthorized("API Key 无效", code=40131)
    if api_key.is_revoked:
        raise Unauthorized("API Key 已被吊销", code=40132)
    if api_key.expires_at and api_key.expires_at < datetime.now(UTC):
        raise Unauthorized("API Key 已过期", code=40133)

    # 应用层二次查询用户（无 FK 关联）
    user_repo = UserRepository(db)
    user = await user_repo.get_by_db_id(api_key.user_id)
    if not user:
        raise Unauthorized("API Key 关联的用户不存在", code=40134)
    if user.status != "active":
        raise Unauthorized("用户已被禁用", code=40108)

    # 更新最后使用时间（失败不影响主流程）
    try:
        await key_repo.touch_last_used(api_key)
    except Exception:
        pass

    return user


ApiKeyUser = Annotated[UserOrm, Depends(_get_api_key_user)]


# ---------- 来自 app.state 的单例 ----------
def _app_state(name: str):
    """返回一个从 request.app.state.<name> 取值的依赖函数."""

    def _dep(request: Request):
        obj = getattr(request.app.state, name, None)
        if obj is None:
            raise RuntimeError(f"app.state.{name} 未初始化, 请检查 lifespan")
        return obj

    return _dep


# _app_state 辅助函数保留，KB 路由通过 request.app.state 手动提取服务实例
