"""API Key 仓储。"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.infrastructure.database.orm.api_key_orm import UserApiKey


class ApiKeyRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, apikey_id: str | int) -> UserApiKey | None:
        """按雪花 ID (str(id) 或 int) 查找。"""
        try:
            kid = int(apikey_id)
        except (TypeError, ValueError):
            return None
        res = await self.db.execute(
            select(UserApiKey).where(UserApiKey.id == kid)
        )
        return res.scalar_one_or_none()

    async def get_by_hash(self, key_hash: str) -> UserApiKey | None:
        """按 key 哈希查找，返回 API Key 记录（不含 user 关联）。"""
        res = await self.db.execute(
            select(UserApiKey).where(UserApiKey.key_hash == key_hash)
        )
        return res.scalar_one_or_none()

    async def list_by_user(self, user_db_id: int) -> Sequence[UserApiKey]:
        """列出某用户的所有 API Key（user_db_id 为 users.id BIGINT）。"""
        res = await self.db.execute(
            select(UserApiKey)
            .where(UserApiKey.user_id == user_db_id)
            .order_by(UserApiKey.created_at.desc())
        )
        return res.scalars().all()

    async def create(
        self,
        *,
        user_db_id: int,
        name: str,
        key_hash: str,
        prefix: str,
        expires_at: str | None = None,
    ) -> UserApiKey:
        key = UserApiKey(
            user_id=user_db_id,
            name=name,
            key_hash=key_hash,
            prefix=prefix,
            expires_at=expires_at,
        )
        self.db.add(key)
        await self.db.flush()
        await self.db.refresh(key)
        return key

    async def revoke(self, key: UserApiKey) -> UserApiKey:
        key.is_revoked = True
        await self.db.flush()
        return key

    async def touch_last_used(self, key: UserApiKey) -> None:
        key.last_used_at = datetime.now(UTC)
        await self.db.flush()
