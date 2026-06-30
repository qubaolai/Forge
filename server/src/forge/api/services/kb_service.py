"""KB 编排服务: KB CRUD + 文档管理 + 检索测试.

定位:
    - 组合现有 Repo / 文件存储 / 入库引擎 / 任务队列, 自身不持久化新状态。
    - 路由层每请求构造一个实例 (传入 DbSession)。
    - 事务: 服务内部只 flush (Repo 行为), commit 由路由统一控制。
    - 删除文档 / KB 时同步清库外索引 (向量 + BM25), 失败仅日志不阻断 DB 删除。

入库是异步的: upload_document 只落盘 + 建 pending 行; 真正解析/切分/向量化由
路由 commit 后 submit("kb.document.ingest") 触发后台任务推进状态机。
"""

from __future__ import annotations

import hashlib
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.schemas.knowledge_base import (
    KbCreateIn,
    KbDocumentInfo,
    KbInfo,
    KbSearchHit,
    KbUpdateIn,
)
from forge.config.domains.paths import uploads_dir
from forge.core.exceptions import BadRequest, Conflict, NotFound
from forge.infrastructure.database.orm.kb_document_orm import KbDocumentOrm
from forge.infrastructure.database.orm.knowledge_base_orm import KnowledgeBaseOrm
from forge.infrastructure.database.repositories.kb_document_repo import (
    KbDocumentRepository,
)
from forge.infrastructure.database.repositories.knowledge_base_repo import (
    KnowledgeBaseRepository,
)
from forge.infrastructure.storage.local_fs import LocalFileStorage

logger = logging.getLogger(__name__)

_SEARCH_HIT_MAX_CHARS = 4000


def to_kb_info(kb: KnowledgeBaseOrm) -> KbInfo:
    """KnowledgeBaseOrm → KbInfo (对外 id 用 str)."""
    return KbInfo(
        id=str(kb.id),
        name=kb.name,
        description=kb.description,
        visibility=kb.visibility,
        owner_id=str(kb.owner_id),
        collaborators=kb.collaborators or [],
        chunk_size=kb.chunk_size,
        chunk_overlap=kb.chunk_overlap,
        document_count=kb.document_count,
        chunk_count=kb.chunk_count,
        size_bytes=kb.size_bytes,
        created_at=kb.created_at,
        updated_at=kb.updated_at,
    )


def to_doc_info(doc: KbDocumentOrm) -> KbDocumentInfo:
    """KbDocumentOrm → KbDocumentInfo (含状态机 + 向量状态字段)."""
    return KbDocumentInfo(
        id=str(doc.id),
        kb_id=str(doc.kb_id),
        name=doc.name,
        source=doc.source,
        source_url=doc.source_url,
        mime_type=doc.mime_type,
        size_bytes=doc.size_bytes,
        content_hash=doc.content_hash,
        status=doc.status,
        status_message=doc.status_message,
        progress=doc.progress,
        chunk_count=doc.chunk_count,
        indexed_at=doc.indexed_at,
        embedding_model_id=(
            str(doc.embedding_model_id) if doc.embedding_model_id is not None else None
        ),
        vector_index_status=doc.vector_index_status,
        vector_index_error=doc.vector_index_error,
        vector_indexed_at=doc.vector_indexed_at,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


def _try_ingest_service():
    """构造用于清库外索引的 KbIngestService; RAG 未装配时返回 None。"""
    try:
        from forge.retrieval.rag_runtime import get_rag_runtime

        runtime = get_rag_runtime()
    except Exception:  # noqa: BLE001
        logger.warning("RAG runtime 未就绪, 跳过库外索引清理")
        return None
    from forge.api.services.kb_ingest_service import KbIngestService
    from forge.retrieval.parsers.dispatcher import default_dispatcher

    return KbIngestService(
        bm25_store=runtime.bm25_store,
        rag_runtime=runtime,
        parser_dispatcher=default_dispatcher(),
    )


class KbService:
    """KB 业务编排. 每请求一个实例."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.kb_repo = KnowledgeBaseRepository(db)
        self.doc_repo = KbDocumentRepository(db)

    # ------------------------------------------------------------------
    # KB CRUD
    # ------------------------------------------------------------------
    async def create_kb(self, *, owner_id: str, body: KbCreateIn) -> KnowledgeBaseOrm:
        kb = KnowledgeBaseOrm(
            name=body.name,
            description=body.description,
            visibility=body.visibility,
            owner_id=int(owner_id),
            chunk_size=body.chunk_size,
            chunk_overlap=body.chunk_overlap,
        )
        await self.kb_repo.create(kb)
        # created_at/updated_at 是 server_default, flush 后对象上仍未填充;
        # refresh 回填, 否则随后 to_kb_info 读时间戳会触发 lazy load 报错。
        await self.db.refresh(kb)
        return kb

    async def list_kbs(self, user_id: str) -> list[KnowledgeBaseOrm]:
        return await self.kb_repo.list_for_user(user_id)

    async def get_readable(self, kb_id: str, user_id: str) -> KnowledgeBaseOrm:
        """读鉴权: 不存在 / 无权统一 404 (不泄露存在性)."""
        kb = await self.kb_repo.get_readable(kb_id, user_id)
        if kb is None:
            raise NotFound("知识库不存在或无权访问", code=40420)
        return kb

    async def get_owned(self, kb_id: str, user_id: str) -> KnowledgeBaseOrm:
        """写鉴权: 仅 owner; 否则 404."""
        kb = await self.kb_repo.get_owned(kb_id, user_id)
        if kb is None:
            raise NotFound("知识库不存在或无权访问", code=40420)
        return kb

    async def update_kb(self, kb: KnowledgeBaseOrm, body: KbUpdateIn) -> KnowledgeBaseOrm:
        if body.name is not None:
            kb.name = body.name
        if body.description is not None:
            kb.description = body.description
        if body.visibility is not None:
            kb.visibility = body.visibility
        next_chunk_size = kb.chunk_size
        next_chunk_overlap = kb.chunk_overlap
        if body.chunk_size is not None:
            next_chunk_size = body.chunk_size
        if body.chunk_overlap is not None:
            next_chunk_overlap = body.chunk_overlap
        if next_chunk_overlap >= next_chunk_size:
            raise BadRequest("chunk_overlap 必须小于 chunk_size", code=40024)
        kb.chunk_size = next_chunk_size
        kb.chunk_overlap = next_chunk_overlap
        await self.db.flush()
        # onupdate=func.now() 由 DB 生成, refresh 拿到最新 updated_at
        await self.db.refresh(kb)
        return kb

    async def delete_kb(self, kb: KnowledgeBaseOrm) -> None:
        """删除 KB: 先清每篇文档的库外索引, 再删 KB (DB CASCADE 带走文档/块)."""
        docs, _ = await self.doc_repo.list_by_kb(str(kb.id), page=1, page_size=100000)
        ingest = _try_ingest_service()
        if ingest is not None:
            for doc in docs:
                try:
                    await ingest.delete_document(session=self.db, document=doc)
                except Exception:  # noqa: BLE001
                    logger.exception("删除 KB 时清库外索引失败 doc=%s", doc.id)
        await self.kb_repo.delete(kb)

    # ------------------------------------------------------------------
    # 文档
    # ------------------------------------------------------------------
    async def upload_document(
        self,
        *,
        kb: KnowledgeBaseOrm,
        filename: str,
        data: bytes,
        mime_type: str | None,
    ) -> KbDocumentOrm:
        """落盘 + 建 pending 文档行 (不触发入库, 由路由 commit 后 submit 任务)."""
        content_hash = hashlib.sha256(data).hexdigest()
        existing = await self.doc_repo.find_by_content_hash(str(kb.id), content_hash)
        if existing is not None:
            raise Conflict(f"相同内容文档已存在: {existing.name}", code=40920)

        # 先建行拿 doc_id (storage_path = {kb_id}/{doc_id}/{filename} 需要 doc_id)
        doc = KbDocumentOrm(
            kb_id=kb.id,
            name=filename,
            source="upload",
            mime_type=mime_type or "application/octet-stream",
            size_bytes=len(data),
            content_hash=content_hash,
            status="pending",
            progress=0,
            vector_index_status="stale",
        )
        await self.doc_repo.create(doc)  # flush 拿 id

        stored = LocalFileStorage(uploads_dir()).save(
            kb_id=str(kb.id),
            document_id=str(doc.id),
            filename=filename,
            data=data,
        )
        doc.storage_path = stored.storage_path
        await self.db.flush()

        await self.kb_repo.update_stats(
            str(kb.id), document_count_delta=1, size_bytes_delta=len(data)
        )
        return doc

    async def list_documents(
        self,
        kb_id: str,
        *,
        status: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[KbDocumentOrm], int]:
        return await self.doc_repo.list_by_kb(
            kb_id, status=status, page=page, page_size=page_size
        )

    async def get_document(self, kb_id: str, doc_id: str) -> KbDocumentOrm:
        doc = await self.doc_repo.get_in_kb(doc_id, kb_id)
        if doc is None:
            raise NotFound("文档不存在", code=40421)
        return doc

    async def delete_document(self, kb: KnowledgeBaseOrm, doc: KbDocumentOrm) -> None:
        """删除文档: 清库外索引 + 删 DB 行 + 回退 KB 统计."""
        ingest = _try_ingest_service()
        if ingest is not None:
            try:
                await ingest.delete_document(session=self.db, document=doc)
            except Exception:  # noqa: BLE001
                logger.exception("删除文档时清库外索引失败 doc=%s", doc.id)
        size = doc.size_bytes or 0
        chunk_n = doc.chunk_count or 0
        await self.doc_repo.delete(doc)
        await self.kb_repo.update_stats(
            str(kb.id),
            document_count_delta=-1,
            chunk_count_delta=-chunk_n,
            size_bytes_delta=-size,
        )

    # ------------------------------------------------------------------
    # 检索测试
    # ------------------------------------------------------------------
    async def search(
        self,
        kb: KnowledgeBaseOrm,
        *,
        query: str,
        top_n: int,
    ) -> list[KbSearchHit]:
        from forge.retrieval.search import search_chunks

        results = await search_chunks(
            self.db, kb_ids=[str(kb.id)], query=query, top_n=top_n
        )
        return [
            KbSearchHit(
                chunk_id=str(r.chunk_id),
                document_id=str(r.document_id),
                document_name=r.document_name,
                kb_name=r.kb_name,
                content=_truncate_search_hit(r.content),
                score=r.final_score,
                page=r.page,
                header_path=r.header_path,
                source_url=r.source_url,
            )
            for r in results
        ]


def _truncate_search_hit(content: str) -> str:
    text = content or ""
    if len(text) <= _SEARCH_HIT_MAX_CHARS:
        return text
    return text[:_SEARCH_HIT_MAX_CHARS] + "...(已截断)"
