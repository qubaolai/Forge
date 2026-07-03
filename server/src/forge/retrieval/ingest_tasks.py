"""KB 文档入库 / 单文档向量重建后台任务.

与 rebuild_tasks.py (admin 全量重建) 平行: 上传 API 落盘 + 建 pending 行后,
submit("kb.document.ingest", document_id=...) 触发本任务异步入库, 推进
kb_documents 状态机 (pending → parsing → chunking → embedding → indexed)。

为什么自装配 RAG 组件:
    任务可能跑在独立 Celery worker 进程, 不能依赖 FastAPI app.state, 因此每个
    任务自行 init_engine + 组装 bm25_store / runtime / dispatcher (与
    rebuild_tasks 同一套路)。

事务语义:
    - 成功: ingest 用同一 session 推进状态机 + 写父块, 任务统一 commit。
    - 失败: rollback 清掉半成品父块 (库外存储由 ingest 内部补偿清理), 再用
      新 session 单独落 failed 状态 + 失败原因。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# 失败原因落库截断长度 (status_message / vector_index_error 都是 Text, 截断防爆)
_MAX_ERR_CHARS = 2000

try:
    from celery import shared_task
except ImportError:  # pragma: no cover

    def shared_task(*args: Any, **kwargs: Any):
        def decorator(fn):
            return fn

        return decorator


def _build_ingest_service():
    """装配 KbIngestService (兼容独立 worker 进程)。返回 (ingest, settings)。"""
    from forge.api.services.kb_ingest_service import KbIngestService
    from forge.config.settings import get_settings
    from forge.infrastructure.database.database import init_engine
    from forge.retrieval.chunkers import ChunkConfig
    from forge.retrieval.common.tokenizer import TokenizerFactory
    from forge.retrieval.parsers.dispatcher import default_dispatcher
    from forge.retrieval.rag_runtime import RagRuntime
    from forge.retrieval.stores.bm25.factory import BM25StoreFactory

    init_engine()
    settings = get_settings()
    tokenizer = TokenizerFactory.create(
        settings.bm25_store.tokenizer.type,
        settings.bm25_store.tokenizer.model_dump(exclude={"type"}),
    )
    bm25_store = BM25StoreFactory.create(
        settings.bm25_store.provider, settings.bm25_store.active_config(), tokenizer
    )
    runtime = RagRuntime(settings=settings, bm25_store=bm25_store)
    ingest = KbIngestService(
        bm25_store=bm25_store,
        rag_runtime=runtime,
        parser_dispatcher=default_dispatcher(),
        chunk_config=ChunkConfig(
            child_target_chars=settings.ingest.chunking.chunk_size,
            child_overlap_chars=settings.ingest.chunking.chunk_overlap,
            table_child_max_chars=settings.ingest.chunking.table_child_max_chars,
        ),
    )
    return ingest, settings


async def _mark_doc_failed(document_id: str, message: str, *, vector: bool = False) -> None:
    """用独立 session 落文档失败状态 (主流程 rollback 后兜底)。"""
    from forge.infrastructure.database.database import get_session_factory
    from forge.infrastructure.database.orm.kb_document_orm import KbDocumentOrm

    factory = get_session_factory()
    async with factory() as db:
        doc = await db.get(KbDocumentOrm, int(document_id))
        if doc is None:
            return
        if vector:
            doc.vector_index_status = "failed"
            doc.vector_index_error = message[:_MAX_ERR_CHARS]
        else:
            doc.status = "failed"
            doc.status_message = message[:_MAX_ERR_CHARS]
        await db.commit()


async def run_kb_ingest_task(document_id: str) -> None:
    """完整入库一篇文档 (异步)。"""
    from forge.config.domains.paths import uploads_dir
    from forge.infrastructure.database.database import get_session_factory
    from forge.infrastructure.database.orm.kb_document_orm import KbDocumentOrm
    from forge.infrastructure.database.orm.knowledge_base_orm import KnowledgeBaseOrm

    ingest, _settings = _build_ingest_service()
    factory = get_session_factory()
    async with factory() as db:
        doc = await db.get(KbDocumentOrm, int(document_id))
        if doc is None:
            logger.warning("KB 入库任务: 文档不存在 doc=%s", document_id)
            return
        kb = await db.get(KnowledgeBaseOrm, doc.kb_id)
        if kb is None:
            logger.warning("KB 入库任务: KB 不存在 doc=%s kb=%s", document_id, doc.kb_id)
            await _mark_doc_failed(document_id, "所属知识库不存在")
            return
        if not doc.storage_path:
            await _mark_doc_failed(document_id, "文档缺少 storage_path, 无法入库")
            return
        file_path = uploads_dir() / doc.storage_path
        try:
            await ingest.ingest(session=db, kb=kb, document=doc, file_path=file_path)
            await db.commit()
            logger.info("KB 入库任务完成 doc=%s", document_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("KB 入库任务失败 doc=%s", document_id)
            await db.rollback()
            await _mark_doc_failed(document_id, f"入库失败: {exc}")


async def run_kb_rebuild_task(document_id: str) -> None:
    """重建单篇文档的向量索引 (异步)。不改 BM25 / 父块 / 主状态。"""
    from forge.config.domains.paths import uploads_dir
    from forge.infrastructure.database.database import get_session_factory
    from forge.infrastructure.database.orm.kb_document_orm import KbDocumentOrm
    from forge.infrastructure.database.orm.knowledge_base_orm import KnowledgeBaseOrm

    ingest, _settings = _build_ingest_service()
    factory = get_session_factory()

    # 第一段: 置 rebuilding (短事务, 让前端尽快看到状态翻转)
    async with factory() as db:
        doc = await db.get(KbDocumentOrm, int(document_id))
        if doc is None:
            logger.warning("KB 重建任务: 文档不存在 doc=%s", document_id)
            return
        if doc.status != "indexed":
            await _mark_doc_failed(document_id, "文档尚未入库完成, 无法重建向量索引", vector=True)
            return
        if not doc.storage_path:
            await _mark_doc_failed(document_id, "文档缺少 storage_path, 无法重建向量索引", vector=True)
            return
        doc.vector_index_status = "rebuilding"
        doc.vector_index_error = None
        await db.commit()

    # 第二段: 实际重建
    async with factory() as db:
        doc = await db.get(KbDocumentOrm, int(document_id))
        if doc is None:
            return
        kb = await db.get(KnowledgeBaseOrm, doc.kb_id)
        if kb is None:
            await _mark_doc_failed(document_id, "所属知识库不存在", vector=True)
            return
        file_path = uploads_dir() / doc.storage_path
        try:
            await ingest.rebuild_vector_index(
                session=db,
                kb=kb,
                document=doc,
                file_path=file_path,
            )
            await db.commit()
            logger.info("KB 重建任务完成 doc=%s", document_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("KB 重建任务失败 doc=%s", document_id)
            await db.rollback()
            await _mark_doc_failed(document_id, f"重建失败: {exc}", vector=True)


@shared_task(name="kb.document.ingest", bind=True, max_retries=1, default_retry_delay=30)
def kb_ingest_task(self: Any, document_id: str) -> None:
    asyncio.run(run_kb_ingest_task(document_id))


@shared_task(name="kb.document.rebuild", bind=True, max_retries=1, default_retry_delay=30)
def kb_rebuild_task(self: Any, document_id: str) -> None:
    asyncio.run(run_kb_rebuild_task(document_id))
