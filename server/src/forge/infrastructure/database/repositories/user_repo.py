"""用户仓储。

约定:
- 只做数据访问,不调用其他层、不写业务规则
- 只 flush 不 commit,事务边界由 get_db 控制
- ID 统一为雪花主键; get_by_id 接收其字符串形式 (str(id))
"""

from collections.abc import Sequence
from typing import Annotated

from fastapi import Depends
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.dependencies import DbSession
from forge.core.security import hash_password
from forge.infrastructure.database.orm.user_orm import UserOrm


def _to_int(value: str | int | None) -> int | None:
    """把对外 ID (str(雪花)) 解析为 BIGINT; 非法/空返回 None。"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class UserRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, user_id: str | int) -> UserOrm | None:
        """按雪花 ID (str(id) 或 int) 查找。"""
        uid = _to_int(user_id)
        if uid is None:
            return None
        res = await self.db.execute(
            select(UserOrm).where(UserOrm.id == uid)
        )
        return res.scalar_one_or_none()

    async def get_by_db_id(self, db_id: int) -> UserOrm | None:
        """按 BIGINT 主键查找 (与 get_by_id 等价, 保留兼容调用)。"""
        res = await self.db.execute(
            select(UserOrm).where(UserOrm.id == db_id)
        )
        return res.scalar_one_or_none()

    async def get_by_email(self, email: str) -> UserOrm | None:
        res = await self.db.execute(select(UserOrm).where(UserOrm.email == email))
        return res.scalar_one_or_none()

    async def get_by_ids(self, user_ids: list[str]) -> Sequence[UserOrm]:
        """按雪花 ID 批量查找。"""
        ids = [i for i in (_to_int(x) for x in user_ids) if i is not None]
        if not ids:
            return []
        res = await self.db.execute(
            select(UserOrm).where(UserOrm.id.in_(ids))
        )
        return res.scalars().all()

    async def list_all(
        self, page: int, page_size: int, q: str = ""
    ) -> tuple[Sequence[UserOrm], int]:
        stmt = select(UserOrm)
        cnt = select(func.count(UserOrm.id))
        if q:
            like = f"%{q}%"
            stmt = stmt.where(
                or_(UserOrm.name.like(like), UserOrm.email.like(like))
            )
            cnt = cnt.where(
                or_(UserOrm.name.like(like), UserOrm.email.like(like))
            )
        stmt = (
            stmt.order_by(UserOrm.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = (await self.db.execute(stmt)).scalars().all()
        total = (await self.db.execute(cnt)).scalar_one()
        return items, total

    async def create(
        self,
        *,
        email: str,
        password: str,
        name: str,
        role: str = "member",
        avatar_url: str | None = None,
        status: str = "active",
    ) -> UserOrm:
        user = UserOrm(
            email=email,
            name=name,
            role=role,
            avatar_url=avatar_url,
            status=status,
            password_hash=hash_password(password),
        )
        self.db.add(user)
        await self.db.flush()
        await self.db.refresh(user)
        return user

    async def update_status(self, user: UserOrm, status: str) -> UserOrm:
        user.status = status
        await self.db.flush()
        return user

    async def update_password(self, user: UserOrm, new_password: str) -> UserOrm:
        user.password_hash = hash_password(new_password)
        await self.db.flush()
        return user

    async def save(self, user: UserOrm) -> UserOrm:
        await self.db.flush()
        return user

    async def delete(self, user: UserOrm) -> None:
        await self.db.delete(user)


def get_user_repo(db: DbSession) -> UserRepository:
    return UserRepository(db)


UserRepoDep = Annotated[UserRepository, Depends(get_user_repo)]
