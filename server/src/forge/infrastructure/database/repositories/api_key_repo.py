"""API Key 仓储。"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.infrastructure.database.orm.api_key_orm import UserApiKey


class ApiKeyRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, key_id: str) -> UserApiKey | None:
        """根据记录 ID 查找 API Key。"""
        res = await self.db.execute(
            select(UserApiKey).where(UserApiKey.id == key_id)
        )
        return res.scalar_one_or_none()

    async def get_by_hash(self, key_hash: str) -> UserApiKey | None:
        """根据 key 哈希查找有效的 API Key（含关联 user）。"""
        from sqlalchemy.orm import joinedload

        res = await self.db.execute(
            select(UserApiKey)
            .options(joinedload(UserApiKey.user))
            .where(UserApiKey.key_hash == key_hash)
        )
        return res.scalar_one_or_none()

    async def list_by_user(self, user_id: str) -> Sequence[UserApiKey]:
        """列出某用户的所有 API Key（含已吊销）。"""
        res = await self.db.execute(
            select(UserApiKey)
            .where(UserApiKey.user_id == user_id)
            .order_by(UserApiKey.created_at.desc())
        )
        return res.scalars().all()

    async def create(
        self,
        *,
        user_id: str,
        name: str,
        key_hash: str,
        prefix: str,
        expires_at: str | None = None,
    ) -> UserApiKey:
        key = UserApiKey(
            user_id=user_id,
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
        """更新最后使用时间（轻量操作，直接 update 避免 flush 全量）。"""
        from datetime import UTC, datetime

        key.last_used_at = datetime.now(UTC)
        await self.db.flush()
