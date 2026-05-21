"""依赖注入.

认证模式 (兼容两套):
    - ``AuthenticatedUser`` (JWT, 用于 Web / 多端登录场景)
        客户端从 ``/auth/login`` 拿 access_token, 后续 ``Authorization: Bearer ...``
        进程内 ``UserCache`` (TTL) 减轻 DB 压力.
    - ``CurrentUser`` (本地 token, 单机 CLI 场景)
        若 ``~/.assistant/local_token`` 存在, 强校验 ``X-Local-Token`` header;
        否则放行, 返回固定 ``LocalUser``.

两者并存: 路由按场景选其中一个. ``AdminUser`` 走 ``CurrentUser`` 链 + 角色校验.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from config import paths
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


# ---------- 本地单用户 (CLI / 本机) ----------
@dataclass(frozen=True)
class LocalUser:
    """单机模式的固定本地用户."""

    id: str = "local"
    email: str = "local@localhost"
    name: str = "Local User"
    avatar_url: str | None = None
    role: str = "owner"
    status: str = "active"


def _read_local_token() -> str | None:
    """读取本机 token. 缺失时返回 None (表示不强制 header)."""
    token_path: Path = paths.local_token_path()
    if not token_path.exists():
        return None
    try:
        token = token_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


async def _get_local_user(
    x_local_token: Annotated[str | None, Header(alias="X-Local-Token")] = None,
) -> LocalUser:
    expected = _read_local_token()
    if expected and x_local_token != expected:
        raise Unauthorized("X-Local-Token 无效", code=40120)
    return LocalUser()


CurrentUser = Annotated[LocalUser, Depends(_get_local_user)]


# ---------- 管理员 ----------
async def _require_admin(user: CurrentUser) -> LocalUser:
    from forge.core.exceptions import Forbidden

    if user.role not in ("owner", "admin"):
        raise Forbidden("仅管理员可访问", code=40311)
    return user


AdminUser = Annotated[LocalUser, Depends(_require_admin)]


# ---------- 来自 app.state 的单例 ----------
def _app_state(name: str):
    """返回一个从 request.app.state.<name> 取值的依赖函数."""

    def _dep(request: Request):
        obj = getattr(request.app.state, name, None)
        if obj is None:
            raise RuntimeError(f"app.state.{name} 未初始化, 请检查 lifespan")
        return obj

    return _dep


# RAG 栈依赖 (lifespan 装配后挂在 app.state)
Retriever = Annotated[object, Depends(_app_state("retriever"))]
KbIngestServiceDep = Annotated[object, Depends(_app_state("kb_ingest_service"))]
FileStorageDep = Annotated[object, Depends(_app_state("file_storage"))]
