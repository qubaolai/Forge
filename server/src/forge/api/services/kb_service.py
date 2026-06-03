"""KB CRUD 业务服务.

定位:
    - 处理 KB / KB 文档的元数据 CRUD (建/读/改/删)
    - 文件存储与索引装载交给 KbIngestService 和 FileStorage
    - 权限校验 (owner_id) 在这里统一做, route 层只负责调用

事务约定:
    - 每个公开方法接收外部 AsyncSession, 不开新事务
    - 仅 flush, 不 commit; commit 由 route 层 (FastAPI 依赖出栈) 控制
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from forge.core.exceptions import BadRequest, NotFound
from forge.infrastructure.database.orm.kb_document_orm import KbDocumentOrm
from forge.infrastructure.database.orm.knowledge_base_orm import (
    KnowledgeBaseOrm,
)

# 直接注入具体 repo 类。
from forge.infrastructure.database.repositories.kb_document_repo import (
    KbDocumentRepository,
)
from forge.infrastructure.database.repositories.knowledge_base_repo import (
    KnowledgeBaseRepository,
)
from forge.infrastructure.storage.base import FileStorage

logger = logging.getLogger(__name__)


class KbService:
    """KB CRUD 业务层 (无状态, 注入存储 / ingest 即可使用)."""

    def __init__(
        self,
        *,
        file_storage: FileStorage,
        kb_ingest_service=None,
    ):
        self._storage = file_storage
        # 解耦循环 import: KbIngestService 实例在 lifespan 装配后注入
        self._ingest = kb_ingest_service

    def set_ingest_service(self, ingest_service) -> None:
        """lifespan 启动后回填 ingest service."""
        self._ingest = ingest_service

    # ==================================================================
    # KB CRUD
    # ==================================================================
    async def create_kb(
        self,
        session: AsyncSession,
        *,
        user_id: str,
        name: str,
        description: str | None,
        visibility: str,
        chunk_size: int,
        chunk_overlap: int,
    ) -> KnowledgeBaseOrm:
        kb = KnowledgeBaseOrm(
            name=name,
            description=description,
            visibility=visibility,
            owner_id=int(user_id),
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        await KnowledgeBaseRepository(session).create(kb)
        logger.info("KB 创建: id=%s name=%s owner=%s", kb.id, kb.name, user_id)
        return kb

    async def list_kbs_for_user(
        self,
        session: AsyncSession,
        *,
        user_id: str,
    ) -> list[KnowledgeBaseOrm]:
        return await KnowledgeBaseRepository(session).list_for_user(user_id)

    async def get_kb(
        self,
        session: AsyncSession,
        *,
        kb_id: str,
        user_id: str,
    ) -> KnowledgeBaseOrm:
        _ = user_id
        kb = await KnowledgeBaseRepository(session).get(kb_id)
        if kb is None:
            raise NotFound(f"KB 不存在: {kb_id}", code=40410)
        return kb

    async def delete_kb(
        self,
        session: AsyncSession,
        *,
        kb_id: str,
        user_id: str,
    ) -> None:
        """硬删除 KB. CASCADE 带走 kb_documents 与 kb_document_chunks,
        本服务再主动清理 Chroma / BM25 / 本地文件 (库外存储)."""
        kb_repo = KnowledgeBaseRepository(session)
        doc_repo = KbDocumentRepository(session)
        kb = await kb_repo.get_owned(kb_id, user_id)
        if kb is None:
            raise NotFound("KB 不存在或无权限", code=40411)

        # 先取所有文档, 用 ingest service 清库外
        docs, _ = await doc_repo.list_by_kb(kb_id, page=1, page_size=10000)
        if self._ingest is not None:
            for doc in docs:
                await self._ingest.delete_document(session=session, document=doc)
                if doc.storage_path:
                    try:
                        self._storage.delete(doc.storage_path)
                    except Exception:  # noqa: BLE001
                        logger.exception("删除原文件失败: %s", doc.storage_path)
        else:
            logger.warning("KbIngestService 未装配, KB 删除时跳过库外清理 (脏数据风险)")

        await kb_repo.delete(kb)
        logger.info("KB 删除完成: %s", kb_id)

    # ==================================================================
    # 文档 CRUD + 上传
    # ==================================================================
    async def list_documents(
        self,
        session: AsyncSession,
        *,
        kb_id: str,
        user_id: str,
        page: int,
        page_size: int,
        status: str | None = None,
    ) -> tuple[list[KbDocumentOrm], int]:
        await self.get_kb(session, kb_id=kb_id, user_id=user_id)
        return await KbDocumentRepository(session).list_by_kb(
            kb_id, status=status, page=page, page_size=page_size
        )

    async def get_document(
        self,
        session: AsyncSession,
        *,
        kb_id: str,
        document_id: str,
        user_id: str,
    ) -> KbDocumentOrm:
        await self.get_kb(session, kb_id=kb_id, user_id=user_id)
        doc = await KbDocumentRepository(session).get_in_kb(document_id, kb_id)
        if doc is None:
            raise NotFound("文档不存在", code=40412)
        return doc

    async def upload_document(
        self,
        session: AsyncSession,
        *,
        kb_id: str,
        user_id: str,
        filename: str,
        mime_type: str,
        content: bytes,
    ) -> KbDocumentOrm:
        """同步上传 + 入库流水线.

        流程:
            1. KB 鉴权 (owner only, 上传是写操作)
            2. content_hash 去重
            3. 创建 kb_documents 行 (status=pending)
            4. FileStorage 落盘
            5. KbIngestService.ingest() 同步驱动完整流水线
            6. 失败时清掉本地文件 + 标记 status=failed
        """
        kb_repo = KnowledgeBaseRepository(session)
        doc_repo = KbDocumentRepository(session)

        kb = await kb_repo.get_owned(kb_id, user_id)
        if kb is None:
            raise NotFound("KB 不存在或无权限", code=40411)

        if not content:
            raise BadRequest("上传内容为空", code=40010)

        content_hash = hashlib.sha256(content).hexdigest()
        existing = await doc_repo.find_by_content_hash(kb_id, content_hash)
        if existing is not None:
            logger.info("重复上传, 复用已有文档: %s", existing.id)
            return existing

        # 1. 落 DB 行 (pending), 拿 doc_id
        doc = KbDocumentOrm(
            kb_id=int(kb_id),
            name=filename,
            source="upload",
            mime_type=mime_type or "application/octet-stream",
            size_bytes=len(content),
            content_hash=content_hash,
            status="pending",
        )
        await doc_repo.create(doc)

        # 2. 写文件
        stored = self._storage.save(
            kb_id=kb_id,
            document_id=doc.id,
            filename=filename,
            data=content,
        )
        doc.storage_path = stored.storage_path
        await session.flush()

        # 3. 同步入库. 失败时本地文件保留 (供调试), status=failed
        if self._ingest is None:
            raise RuntimeError("KbIngestService 未装配 (lifespan 启动失败?), 无法入库")

        try:
            await self._ingest.ingest(
                session=session,
                kb=kb,
                document=doc,
                file_path=Path(self._resolve_storage_abs(stored.storage_path)),
            )
        except Exception:
            # ingest 内部已经把 status 改成 failed, 这里再 raise 让 route 层 rollback
            raise

        # 4. KB 文档计数 +1
        await kb_repo.update_stats(
            kb_id,
            document_count_delta=1,
            size_bytes_delta=len(content),
        )
        return doc

    async def delete_document(
        self,
        session: AsyncSession,
        *,
        kb_id: str,
        document_id: str,
        user_id: str,
    ) -> None:
        """硬删除文档 (元数据 + 索引 + 文件)."""
        kb_repo = KnowledgeBaseRepository(session)
        doc_repo = KbDocumentRepository(session)
        kb = await kb_repo.get_owned(kb_id, user_id)
        if kb is None:
            raise NotFound("KB 不存在或无权限", code=40411)
        doc = await doc_repo.get_in_kb(document_id, kb_id)
        if doc is None:
            raise NotFound("文档不存在", code=40412)

        if self._ingest is not None:
            await self._ingest.delete_document(session=session, document=doc)
        else:
            logger.warning("KbIngestService 未装配, 跳过库外清理")

        if doc.storage_path:
            try:
                self._storage.delete(doc.storage_path)
            except Exception:  # noqa: BLE001
                logger.exception("删除原文件失败: %s", doc.storage_path)

        size_delta = -int(doc.size_bytes or 0)
        chunk_delta = -int(doc.chunk_count or 0)
        await doc_repo.delete(doc)
        await kb_repo.update_stats(
            kb_id,
            document_count_delta=-1,
            chunk_count_delta=chunk_delta,
            size_bytes_delta=size_delta,
        )

    # ==================================================================
    # 内部
    # ==================================================================
    def _resolve_storage_abs(self, storage_path: str) -> str:
        """把 storage_path 解析为本地绝对路径, 给 ingest 用. 仅本地存储有意义,
        对象存储后端需要先 download 再 ingest, 留作 V2."""
        from forge.infrastructure.storage.local_fs import LocalFileStorage

        if not isinstance(self._storage, LocalFileStorage):
            raise RuntimeError(
                "当前 FileStorage 后端不支持本地路径解析 (非 LocalFileStorage), "
                "对象存储模式需要先 download_to_temp"
            )
        return str(self._storage._base / storage_path)
