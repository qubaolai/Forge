"""Agent 仓储。"""

from collections.abc import Sequence
from typing import Annotated

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.dependencies import DbSession
from forge.infrastructure.database.orm.agent_orm import AgentOrm


class AgentRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_accessible(
        self,
        user_id: str,
        page: int,
        page_size: int,
        q: str = "",
    ) -> tuple[Sequence[AgentOrm], int]:
        """单机模式下列出全部 agents."""
        _ = user_id
        stmt = select(AgentOrm)
        cnt = select(func.count(AgentOrm.id))
        if q:
            like = f"%{q}%"
            stmt = stmt.where(AgentOrm.name.like(like))
            cnt = cnt.where(AgentOrm.name.like(like))

        stmt = (
            stmt.order_by(AgentOrm.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = (await self.db.execute(stmt)).scalars().all()
        total = (await self.db.execute(cnt)).scalar_one()
        return items, total

    async def get_by_id(self, agent_id: str) -> AgentOrm | None:
        res = await self.db.execute(select(AgentOrm).where(AgentOrm.id == agent_id))
        return res.scalar_one_or_none()

    async def get_by_ids(self, agent_ids: list[str]) -> Sequence[AgentOrm]:
        if not agent_ids:
            return []
        res = await self.db.execute(select(AgentOrm).where(AgentOrm.id.in_(agent_ids)))
        return res.scalars().all()

    async def create(self, agent: AgentOrm) -> AgentOrm:
        self.db.add(agent)
        await self.db.flush()
        await self.db.refresh(agent)
        return agent

    async def save(self, agent: AgentOrm) -> AgentOrm:
        await self.db.flush()
        return agent

    async def delete(self, agent: AgentOrm) -> None:
        await self.db.delete(agent)


def get_agent_repo(db: DbSession) -> AgentRepository:
    return AgentRepository(db)


AgentRepoDep = Annotated[AgentRepository, Depends(get_agent_repo)]
