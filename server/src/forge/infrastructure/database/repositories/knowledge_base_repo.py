"""知识库 (KB) 仓储.

权限模型:
    - private:   仅 owner 可见
    - workspace: owner + collaborators 可见 (协作者结构见 KnowledgeBaseOrm)
    - public:    全用户可见

knowledge_search 工具用 find_accessible_by_names 同时做"按名查"+"权限过滤":
    工具拿到 LLM 传来的 kb_names + 当前请求 user_id, 这里负责过滤,
    工具不需要自己重复写鉴权.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.infrastructure.database.orm.knowledge_base_orm import (
    KnowledgeBaseOrm,
)
from forge.infrastructure.database.repositories.base import BaseRepository
from forge.infrastructure.storage.data_protocols import KnowledgeBaseStore

logger = logging.getLogger(__name__)


def _to_int(value: str | int | None) -> int | None:
    """对外 ID (str(雪花)) → BIGINT; 非法/空返回 None。"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _collaborator_user_ids(kb: KnowledgeBaseOrm) -> set[str]:
    """从 collaborators JSON 提取协作者 user_id 集合 (统一成 str).

    兼容两种历史形态: [{"user_id": ...}, ...] 或裸 ["uid", ...].
    """
    out: set[str] = set()
    for c in kb.collaborators or []:
        if isinstance(c, dict) and c.get("user_id") is not None:
            out.add(str(c["user_id"]))
        elif isinstance(c, str | int):
            out.add(str(c))
    return out


def _can_read(kb: KnowledgeBaseOrm, user_id: str) -> bool:
    """KB 可读判定: public 任何人可读; private/workspace 需 owner;
    workspace 额外允许 collaborators 命中. 空 user_id 视为匿名, 仅 public.
    """
    if kb.visibility == "public":
        return True
    uid = (user_id or "").strip()
    if not uid:
        return False
    if str(kb.owner_id) == uid:
        return True
    return kb.visibility == "workspace" and uid in _collaborator_user_ids(kb)


class KnowledgeBaseRepository(BaseRepository, KnowledgeBaseStore):
    """知识库 CRUD + 按名/权限查询."""

    def __init__(self, session: AsyncSession):
        self.session = session

    # ------------------------------------------------------------------
    # 创建 / 读 / 更新 / 删除
    # ------------------------------------------------------------------
    async def create(self, kb: KnowledgeBaseOrm) -> KnowledgeBaseOrm:
        """新建 KB. 不 commit, 由调用方控制事务."""
        self.session.add(kb)
        await self.session.flush()
        return kb

    async def get(self, kb_id: str) -> KnowledgeBaseOrm | None:
        return await self.session.get(KnowledgeBaseOrm, _to_int(kb_id))

    async def get_owned(self, kb_id: str, user_id: str) -> KnowledgeBaseOrm | None:
        """写操作鉴权: 仅 owner 可得. 越权 / 不存在统一返回 None,
        由路由折成 404 (不泄露存在性)."""
        kb = await self.session.get(KnowledgeBaseOrm, _to_int(kb_id))
        if kb is None or str(kb.owner_id) != (user_id or "").strip():
            return None
        return kb

    async def get_readable(self, kb_id: str, user_id: str) -> KnowledgeBaseOrm | None:
        """读操作鉴权: owner / collaborator / public 任一可读, 否则 None."""
        kb = await self.session.get(KnowledgeBaseOrm, _to_int(kb_id))
        if kb is None or not _can_read(kb, user_id):
            return None
        return kb

    async def list_for_user(self, user_id: str) -> list[KnowledgeBaseOrm]:
        """列出当前用户可读的 KB: public + 自己拥有 + 协作参与的 workspace.

        单机量级直接全量拉取后应用层按 _can_read 过滤 (collaborators 是 JSON,
        避开 MySQL/SQLite JSON 方言差异); 数据量增大后可改 SQL 收窄.
        """
        uid = (user_id or "").strip()
        stmt = select(KnowledgeBaseOrm).order_by(KnowledgeBaseOrm.created_at.desc())
        result = await self.session.execute(stmt)
        return [kb for kb in result.scalars().all() if _can_read(kb, uid)]

    async def delete(self, kb: KnowledgeBaseOrm) -> None:
        """删除 KB (硬删除, CASCADE 会带走 kb_documents 与 kb_document_chunks)."""
        await self.session.delete(kb)
        await self.session.flush()

    async def update_stats(
        self,
        kb_id: str,
        *,
        document_count_delta: int = 0,
        chunk_count_delta: int = 0,
        size_bytes_delta: int = 0,
    ) -> None:
        """增量更新 KB 统计字段. 不 commit."""
        kb = await self.session.get(KnowledgeBaseOrm, _to_int(kb_id))
        if kb is None:
            return
        kb.document_count = max(0, (kb.document_count or 0) + document_count_delta)
        kb.chunk_count = max(0, (kb.chunk_count or 0) + chunk_count_delta)
        kb.size_bytes = max(0, (kb.size_bytes or 0) + size_bytes_delta)
        await self.session.flush()

    # ------------------------------------------------------------------
    # knowledge_search 工具使用
    # ------------------------------------------------------------------
    async def find_accessible_by_names(
        self,
        names: list[str],
        user_id: str,
    ) -> list[KnowledgeBaseOrm]:
        """按名称查可访问的 KB. 权限规则:

            - public                  → 任何人可读
            - workspace / private 中 owner_id == user_id → 可读
            - workspace 中 user_id 在 collaborators 里     → 可读 (TODO: 暂未实现)

        collaborators 以应用层 _can_read 判定 (避开 JSON 方言); workspace 中
        命中 collaborators 的用户也可读.

        Args:
            names:    KB 显示名列表; 空列表返回 []
            user_id:  当前请求用户 ID; 空字符串视为匿名, 仅能看 public KB

        Returns:
            命中且通过权限校验的 KB ORM 列表. 顺序不保证, 调用方按需排序.
        """
        if not names:
            return []

        stmt = select(KnowledgeBaseOrm).where(KnowledgeBaseOrm.name.in_(names))
        result = await self.session.execute(stmt)
        return [kb for kb in result.scalars().all() if _can_read(kb, user_id)]

    async def find_accessible_by_ids(
        self,
        kb_ids: list[str],
        user_id: str,
    ) -> list[KnowledgeBaseOrm]:
        """按稳定 KB ID 查可访问的 KB。供工具调用优先使用。"""
        ids = [i for i in (_to_int(k) for k in kb_ids) if i is not None]
        if not ids:
            return []
        stmt = select(KnowledgeBaseOrm).where(KnowledgeBaseOrm.id.in_(ids))
        result = await self.session.execute(stmt)
        return [kb for kb in result.scalars().all() if _can_read(kb, user_id)]
