"""认证业务:登录、刷新、登出。"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends

from forge.core.exceptions import Unauthorized
from forge.core.security import (
    create_access_token,
    create_refresh_token,
    get_token_payload,
    verify_password,
)
from forge.infrastructure.database.orm.user_orm import UserOrm
from forge.infrastructure.database.repositories.user_repo import (
    UserRepoDep,
    UserRepository,
)
from forge.infrastructure.database.token_blacklist import TokenBlacklist, TokenBlacklistDep
from forge.infrastructure.database.user_cache import UserCache, UserCacheDep
from forge.utils.id_generator import new_id


class AuthService:
    def __init__(
        self,
        user_repo: UserRepository,
        blacklist: TokenBlacklist,
        user_cache: UserCache,
    ):
        self.user_repo = user_repo
        self.blacklist = blacklist
        self.user_cache = user_cache

    async def authenticate(self, email: str, password: str) -> UserOrm:
        user = await self.user_repo.get_by_email(email)
        if not user:
            # 不区分"邮箱不存在"和"密码错误",防枚举
            raise Unauthorized("邮箱或密码错误", code=40109)
        if not verify_password(password, user.password_hash):
            raise Unauthorized("邮箱或密码错误", code=40109)
        if user.status != "active":
            raise Unauthorized("账号已被禁用", code=40108)
        return user

    def issue_tokens(self, user_id: str) -> tuple[str, datetime, str, datetime, str]:
        """返回 (access, access_exp, refresh, refresh_exp, jti)。"""
        access, access_exp = create_access_token(user_id)
        jti = new_id("jti")
        refresh, refresh_exp = create_refresh_token(user_id, jti)
        return access, access_exp, refresh, refresh_exp, jti

    async def refresh_access(self, refresh_token: str | None) -> tuple[str, datetime, UserOrm]:
        if not refresh_token:
            raise Unauthorized("缺少 refresh token", code=40110)

        payload = get_token_payload(refresh_token, expected_type="refresh")
        jti = payload.get("jti")
        user_id = payload.get("sub")
        if not jti or not user_id:
            raise Unauthorized("refresh token 无效", code=40102)

        if await self.blacklist.is_revoked(jti):
            raise Unauthorized("refresh token 已失效", code=40111)

        user = await self.user_cache.get(user_id)
        if not user or user.status != "active":
            raise Unauthorized("用户不可用", code=40108)

        access, access_exp = create_access_token(user_id)
        return access, access_exp, user

    async def revoke(self, refresh_token: str | None) -> None:
        """登出:把 refresh 拉黑 + 清用户缓存。"""
        if not refresh_token:
            return
        try:
            payload = get_token_payload(refresh_token, expected_type="refresh")
        except Unauthorized:
            return  # token 本身无效,无需拉黑
        jti = payload.get("jti")
        user_id = payload.get("sub")
        exp = payload.get("exp")
        if not jti or not exp:
            return

        expires_at = datetime.fromtimestamp(exp, tz=UTC)
        await self.blacklist.revoke(jti, expires_at)
        if user_id:
            await self.user_cache.invalidate(user_id)


def get_auth_service(
    user_repo: UserRepoDep,
    blacklist: TokenBlacklistDep,
    user_cache: UserCacheDep,
) -> AuthService:
    return AuthService(user_repo, blacklist, user_cache)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
