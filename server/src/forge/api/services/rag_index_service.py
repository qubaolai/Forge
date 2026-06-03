"""RAG 向量索引状态与批量重建编排。"""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from forge.infrastructure.database.orm.kb_document_orm import KbDocumentOrm
from forge.infrastructure.database.orm.rag_index_rebuild_job_orm import RagIndexRebuildJobOrm
from forge.infrastructure.database.orm.system_model_binding_orm import SystemModelBindingOrm


class RagIndexService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def status(self) -> dict:
        rows = (await self.db.execute(
            select(KbDocumentOrm.vector_index_status, func.count())
            .where(KbDocumentOrm.status == "indexed")
            .group_by(KbDocumentOrm.vector_index_status)
        )).all()
        jobs = list((await self.db.execute(
            select(RagIndexRebuildJobOrm).order_by(RagIndexRebuildJobOrm.created_at.desc()).limit(20)
        )).scalars().all())
        return {
            "documents": {str(status): int(count) for status, count in rows},
            "jobs": [self._job_dict(j) for j in jobs],
        }

    async def create_rebuild(self, *, created_by: str | None = None, failed_only: bool = False) -> dict:
        binding = (await self.db.execute(
            select(SystemModelBindingOrm).where(SystemModelBindingOrm.role == "rag_embedding")
        )).scalar_one_or_none()
        if binding is None or binding.model_id is None:
            raise ValueError("尚未配置 RAG Embedding 模型")
        condition = KbDocumentOrm.status == "indexed"
        if failed_only:
            condition = condition & (KbDocumentOrm.vector_index_status == "failed")
        total = int((await self.db.execute(
            select(func.count()).select_from(KbDocumentOrm).where(condition)
        )).scalar_one())
        job = RagIndexRebuildJobOrm(
            model_id=binding.model_id,
            binding_version=binding.version,
            status="pending",
            total_documents=total,
            created_by=int(created_by) if created_by else None,
        )
        self.db.add(job)
        await self.db.flush()
        await self.db.execute(
            update(KbDocumentOrm).where(condition).values(
                vector_index_status="stale",
                vector_index_error=None,
            )
        )
        return self._job_dict(job)

    async def retry(self, job_id: str, *, created_by: str | None = None) -> dict:
        try:
            previous = await self.db.get(RagIndexRebuildJobOrm, int(job_id))
        except (TypeError, ValueError) as exc:
            raise ValueError("重建任务 ID 格式无效") from exc
        if previous is None:
            raise ValueError("重建任务不存在")
        if previous.failed_documents <= 0:
            raise ValueError("该重建任务没有失败文档")
        return await self.create_rebuild(created_by=created_by, failed_only=True)

    @staticmethod
    def _job_dict(job: RagIndexRebuildJobOrm) -> dict:
        return {
            "id": str(job.id),
            "model_id": str(job.model_id),
            "binding_version": job.binding_version,
            "status": job.status,
            "total_documents": job.total_documents,
            "succeeded_documents": job.succeeded_documents,
            "failed_documents": job.failed_documents,
            "error_message": job.error_message,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        }
