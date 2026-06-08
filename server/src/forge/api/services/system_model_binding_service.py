"""系统模型角色绑定服务。"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from forge.infrastructure.database.orm.kb_document_orm import KbDocumentOrm
from forge.infrastructure.database.orm.model_provider_orm import ProviderOrm
from forge.infrastructure.database.orm.system_model_binding_orm import SystemModelBindingOrm
from forge.infrastructure.database.repositories.model_config_repo import ModelConfigRepository
from forge.infrastructure.database.repositories.model_repo import ModelRepository

ROLE_MODEL_TYPES = {
    "rag_embedding": "embedding",
    "semantic_history_embedding": "embedding",
    "rag_reranker": "reranker",
}
OPTIONAL_ROLES = {"semantic_history_embedding", "rag_reranker"}


class SystemModelBindingService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_bindings(self) -> list[dict]:
        rows = list((await self.db.execute(
            select(SystemModelBindingOrm).order_by(SystemModelBindingOrm.role)
        )).scalars().all())
        result = []
        model_repo = ModelRepository(self.db)
        config_repo = ModelConfigRepository(self.db)
        for row in rows:
            model = await model_repo.get_by_id(row.model_id) if row.model_id else None
            result.append({
                "id": str(row.id),
                "role": row.role,
                "model_id": str(row.model_id) if row.model_id else None,
                "version": row.version,
                "optional": row.role in OPTIONAL_ROLES,
                "model": None if model is None else {
                    "id": str(model.id),
                    "model_id": str(model.id),
                    "provider_id": str(model.provider_id),
                    "name": model.name,
                    "display_name": model.display_name,
                    "model_type": model.model_type,
                    "config": config_repo.to_dict(await config_repo.get(model.id, model.model_type)),
                    "is_enabled": model.is_enabled
                },
            })
        return result

    async def set_binding(
        self, role: str, model_id: str | None, *, updated_by: str | None = None
    ) -> dict:
        expected_type = ROLE_MODEL_TYPES.get(role)
        if expected_type is None:
            raise ValueError(f"未知系统模型角色: {role}")
        if model_id is None and role not in OPTIONAL_ROLES:
            raise ValueError(f"{role} 不允许关闭")

        try:
            mid = int(model_id) if model_id else None
        except (TypeError, ValueError) as exc:
            raise ValueError("model_id 格式无效") from exc
        if mid is not None:
            model = await ModelRepository(self.db).get_by_id(mid)
            if model is None or not model.is_enabled:
                raise ValueError("目标模型不存在或未启用")
            provider = await self.db.get(ProviderOrm, model.provider_id)
            if provider is None or not provider.is_enabled:
                raise ValueError("目标模型所属供应商不存在或未启用")
            if model.model_type != expected_type:
                raise ValueError(f"{role} 只能绑定 {expected_type} 模型")
            if await ModelConfigRepository(self.db).get(model.id, model.model_type) is None:
                raise ValueError("目标模型缺少类型配置")

        row = (await self.db.execute(
            select(SystemModelBindingOrm).where(SystemModelBindingOrm.role == role)
        )).scalar_one_or_none()
        if row is None:
            row = SystemModelBindingOrm(role=role, model_id=mid, version=1)
            self.db.add(row)
        else:
            if row.model_id == mid:
                return {"role": role, "model_id": str(mid) if mid else None, "version": row.version}
            row.model_id = mid
            row.version = int(row.version or 0) + 1
        row.updated_by = int(updated_by) if updated_by else None

        if role == "rag_embedding":
            await self.db.execute(
                update(KbDocumentOrm).where(KbDocumentOrm.status == "indexed").values(
                    vector_index_status="stale",
                    vector_index_error=None,
                )
            )
        await self.db.flush()
        return {"role": role, "model_id": str(mid) if mid else None, "version": row.version}
