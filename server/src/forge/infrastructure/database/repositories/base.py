"""Repository基类"""

from sqlalchemy.ext.asyncio import AsyncSession


class BaseRepository:
    """所有 Repository 的基类。只持有 session,不负责事务。"""

    def __init__(self, session: AsyncSession):
        self.session = session
