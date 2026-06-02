"""API Key 管理路由 — 创建 / 列表 / 吊销。

通过 JWT 认证（Web 登录后）管理 API Key。
创建后 Key 明文仅返回一次，用于配置 CLI 远程访问。
"""

from fastapi import APIRouter

from forge.api.dependencies import AuthenticatedUser, DbSession
from forge.api.schemas.api_key import ApiKeyCreateIn, ApiKeyListItem, ApiKeyOut
from forge.core.exceptions import NotFound
from forge.core.response import success
from forge.core.security import extract_api_key_prefix, generate_api_key
from forge.infrastructure.database.repositories.api_key_repo import (
    ApiKeyRepository,
)

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


def _repo(db: DbSession) -> ApiKeyRepository:
    return ApiKeyRepository(db)


@router.post("")
async def create_api_key(
    body: ApiKeyCreateIn,
    user: AuthenticatedUser,
    db: DbSession,
):
    """创建 API Key — 返回原始 key 明文，仅此一次。"""
    raw_key, key_hash = generate_api_key()
    api_key = await _repo(db).create(
        user_db_id=user.id,
        name=body.name,
        key_hash=key_hash,
        prefix=extract_api_key_prefix(raw_key),
    )
    return success(
        ApiKeyOut(
            id=str(api_key.id),
            name=api_key.name,
            prefix=api_key.prefix,
            raw_key=raw_key,
            created_at=api_key.created_at,
        ).model_dump(mode="json")
    )


@router.get("")
async def list_api_keys(
    user: AuthenticatedUser,
    db: DbSession,
):
    """列出当前用户的所有 API Key（不含原始 key 明文）。"""
    keys = await _repo(db).list_by_user(user.id)
    items = [
        ApiKeyListItem(
            id=str(k.id),
            name=k.name,
            prefix=k.prefix,
            last_used_at=k.last_used_at,
            expires_at=k.expires_at,
            is_revoked=k.is_revoked,
            created_at=k.created_at,
        ).model_dump(mode="json")
        for k in keys
    ]
    return success(items)


@router.delete("/{key_id}")
async def revoke_api_key(
    key_id: str,
    user: AuthenticatedUser,
    db: DbSession,
):
    """吊销 API Key（仅所有者可操作）。key_id 为雪花 ID (str(id))。"""
    api_key = await _repo(db).get_by_id(key_id)
    if not api_key:
        raise NotFound("API Key 不存在", code=40430)
    if api_key.user_id != user.id:
        raise NotFound("API Key 不存在", code=40430)
    await _repo(db).revoke(api_key)
    return success(None, message="API Key 已吊销")
