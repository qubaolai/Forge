"""KB 发现 — 查询当前用户可访问的知识库列表。"""

from __future__ import annotations

import logging

from forge.infrastructure.database.database import session_scope
from forge.infrastructure.database.repositories.knowledge_base_repo import KnowledgeBaseRepository

logger = logging.getLogger(__name__)


async def fetch_kb_list(user_id: str) -> list[dict]:
    """查当前用户可访问的 KB。失败时返回空列表（降级，不阻塞对话）。"""
    if not user_id:
        return []
    try:
        async with session_scope() as db:
            kb_repo = KnowledgeBaseRepository(db)
            kbs = await kb_repo.list_for_user(user_id)
            return [
                {
                    "id": str(kb.id),
                    "name": kb.name,
                    "description": (kb.description or "").strip(),
                    "document_count": kb.document_count or 0,
                }
                for kb in kbs
            ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("拉取可用 KB 列表失败 user=%s: %s", user_id, exc)
        return []


async def fetch_selected_kb_list(user_id: str, kb_ids: list[str] | tuple[str, ...]) -> list[dict]:
    """查本轮前端显式选择且当前用户可访问的 KB。失败时返回空列表。"""
    cleaned = [str(kb_id).strip() for kb_id in kb_ids if str(kb_id).strip()]
    if not user_id or not cleaned:
        return []
    try:
        async with session_scope() as db:
            kb_repo = KnowledgeBaseRepository(db)
            kbs = await kb_repo.find_accessible_by_ids(cleaned, user_id)
            by_id = {str(kb.id): kb for kb in kbs}
            return [
                {
                    "id": str(kb.id),
                    "name": kb.name,
                    "description": (kb.description or "").strip(),
                    "document_count": kb.document_count or 0,
                }
                for kb_id in cleaned
                if (kb := by_id.get(kb_id)) is not None
            ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("拉取已选择 KB 列表失败 user=%s ids=%s: %s", user_id, cleaned, exc)
        return []
