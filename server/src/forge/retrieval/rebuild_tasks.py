"""RAG 向量索引批量重建任务。"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select

logger = logging.getLogger(__name__)

try:
    from celery import shared_task
except ImportError:  # pragma: no cover
    def shared_task(*args: Any, **kwargs: Any):
        def decorator(fn):
            return fn
        return decorator


async def run_rag_rebuild_task(job_id: str) -> None:
    from forge.api.services.kb_ingest_service import KbIngestService
    from forge.config.settings import get_settings
    from forge.infrastructure.database.database import get_session_factory, init_engine
    from forge.infrastructure.database.orm.kb_document_orm import KbDocumentOrm
    from forge.infrastructure.database.orm.rag_index_rebuild_job_orm import RagIndexRebuildJobOrm
    from forge.infrastructure.database.orm.system_model_binding_orm import SystemModelBindingOrm
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
        ),
    )
    factory = get_session_factory()
    async with factory() as db:
        job = await db.get(RagIndexRebuildJobOrm, int(job_id))
        if job is None:
            return
        binding = (await db.execute(
            select(SystemModelBindingOrm).where(SystemModelBindingOrm.role == "rag_embedding")
        )).scalar_one_or_none()
        if (
            binding is None
            or binding.version != job.binding_version
            or binding.model_id != job.model_id
        ):
            job.status = "cancelled"
            await db.commit()
            return
        job.status = "running"
        await db.commit()
        docs = list((await db.execute(
            select(KbDocumentOrm).where(
                KbDocumentOrm.status == "indexed",
                KbDocumentOrm.vector_index_status.in_(["stale", "failed"]),
            )
        )).scalars().all())

    for doc in docs:
        async with factory() as db:
            job = await db.get(RagIndexRebuildJobOrm, int(job_id))
            binding = (await db.execute(
                select(SystemModelBindingOrm).where(SystemModelBindingOrm.role == "rag_embedding")
            )).scalar_one_or_none()
            if (
                job is None
                or binding is None
                or binding.version != job.binding_version
                or binding.model_id != job.model_id
            ):
                if job is not None:
                    job.status = "cancelled"
                    await db.commit()
                return
            current = await db.get(KbDocumentOrm, doc.id)
            if current is None:
                continue
            if not current.storage_path:
                current.vector_index_status = "failed"
                current.vector_index_error = "文档缺少 storage_path，无法重建向量索引"
                job.failed_documents += 1
                await db.commit()
                continue
            current.vector_index_status = "rebuilding"
            await db.commit()
            expected_version = job.binding_version
            expected_model_id = job.model_id

            async def ensure_binding_current(
                version: int = expected_version,
                model_id: int = expected_model_id,
            ) -> None:
                latest = (await db.execute(
                    select(SystemModelBindingOrm.version, SystemModelBindingOrm.model_id).where(
                        SystemModelBindingOrm.role == "rag_embedding"
                    )
                )).one_or_none()
                if (
                    latest is None
                    or latest[0] != version
                    or latest[1] != model_id
                ):
                    raise RuntimeError("RAG Embedding 绑定已变化")

            try:
                path = Path(settings.ingest.documents_path) / "uploads" / current.storage_path
                await ingest.rebuild_vector_index(
                    document=current,
                    file_path=path,
                    before_vector_write=ensure_binding_current,
                )
                job.succeeded_documents += 1
            except Exception as exc:  # noqa: BLE001
                binding = (await db.execute(
                    select(SystemModelBindingOrm.version, SystemModelBindingOrm.model_id).where(
                        SystemModelBindingOrm.role == "rag_embedding"
                    )
                )).one_or_none()
                if (
                    binding is None
                    or binding[0] != expected_version
                    or binding[1] != expected_model_id
                ):
                    current.vector_index_status = "stale"
                    job.status = "cancelled"
                    await db.commit()
                    return
                logger.exception("RAG 重建失败 doc=%s", current.id)
                current.vector_index_status = "failed"
                current.vector_index_error = str(exc)
                job.failed_documents += 1
            await db.commit()

    async with factory() as db:
        job = await db.get(RagIndexRebuildJobOrm, int(job_id))
        if job is not None:
            binding = (await db.execute(
                select(SystemModelBindingOrm.version, SystemModelBindingOrm.model_id).where(
                    SystemModelBindingOrm.role == "rag_embedding"
                )
            )).one_or_none()
            if (
                binding is None
                or binding[0] != job.binding_version
                or binding[1] != job.model_id
            ):
                job.status = "cancelled"
            else:
                job.status = "completed" if job.failed_documents == 0 else "partial_failed"
            await db.commit()


@shared_task(name="rag.index.rebuild", bind=True, max_retries=1, default_retry_delay=30)
def rag_rebuild_task(self: Any, job_id: str) -> None:
    asyncio.run(run_rag_rebuild_task(job_id))
