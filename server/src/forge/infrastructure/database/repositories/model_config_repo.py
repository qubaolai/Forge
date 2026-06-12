"""模型类型配置仓储。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.infrastructure.database.orm.model_config_orm import (
    ChatModelConfigOrm,
    EmbeddingModelConfigOrm,
    RerankerModelConfigOrm,
)
from forge.infrastructure.database.orm.model_orm import ModelOrm


class ModelConfigRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self, model_id: int, model_type: str):
        if model_type == "chat":
            chat_stmt = select(ChatModelConfigOrm).where(ChatModelConfigOrm.model_id == model_id)
            return (await self.db.execute(chat_stmt)).scalar_one_or_none()
        elif model_type == "embedding":
            embedding_stmt = select(EmbeddingModelConfigOrm).where(
                EmbeddingModelConfigOrm.model_id == model_id
            )
            return (await self.db.execute(embedding_stmt)).scalar_one_or_none()
        elif model_type == "reranker":
            reranker_stmt = select(RerankerModelConfigOrm).where(
                RerankerModelConfigOrm.model_id == model_id
            )
            return (await self.db.execute(reranker_stmt)).scalar_one_or_none()
        else:
            return None

    async def create(self, model_id: int, model_type: str, config: dict):
        if model_type == "chat":
            await self._validate_model_type(model_id, model_type)
            chat_row = ChatModelConfigOrm(model_id=model_id, **config)
            self.db.add(chat_row)
            await self.db.flush()
            return chat_row
        elif model_type == "embedding":
            await self._validate_model_type(model_id, model_type)
            embedding_row = EmbeddingModelConfigOrm(model_id=model_id, **config)
            self.db.add(embedding_row)
            await self.db.flush()
            return embedding_row
        elif model_type == "reranker":
            await self._validate_model_type(model_id, model_type)
            reranker_row = RerankerModelConfigOrm(model_id=model_id, **config)
            self.db.add(reranker_row)
            await self.db.flush()
            return reranker_row
        else:
            raise ValueError(f"不支持的模型类型: {model_type}")

    async def update(self, model_id: int, model_type: str, config: dict):
        await self._validate_model_type(model_id, model_type)
        row = await self.get(model_id, model_type)
        if row is None:
            return await self.create(model_id, model_type, config)
        for key, value in config.items():
            if hasattr(row, key):
                setattr(row, key, value)
        await self.db.flush()
        return row

    async def _validate_model_type(self, model_id: int, model_type: str) -> None:
        model = await self.db.get(ModelOrm, model_id)
        if model is None:
            raise ValueError(f"模型不存在: id={model_id}")
        if model.model_type != model_type:
            raise ValueError(
                f"模型 {model.name} 的类型为 {model.model_type}，不能写入 {model_type} 配置"
            )

    @staticmethod
    def to_dict(row) -> dict:
        if row is None:
            return {}
        if isinstance(row, ChatModelConfigOrm):
            return {
                "context_window": row.context_window,
                "max_output_tokens": row.max_output_tokens,
                "input_modalities": list(row.input_modalities or []),
                "output_modalities": list(row.output_modalities or []),
                "capabilities": list(row.capabilities or []),
                "thinking_options": row.thinking_options,
                "provider_options": row.provider_options or {},
            }
        if isinstance(row, EmbeddingModelConfigOrm):
            return {
                "dimension": row.dimension,
                "batch_size": row.batch_size,
                "supported_dimensions": list(row.supported_dimensions or []),
                "max_batch_size": row.max_batch_size,
                "input_modalities": list(row.input_modalities or []),
                "max_retries": row.max_retries,
                "retry_backoff": row.retry_backoff,
                "provider_options": row.provider_options or {},
            }
        if isinstance(row, RerankerModelConfigOrm):
            return {
                "timeout_seconds": row.timeout_seconds,
                "max_retries": row.max_retries,
                "retry_backoff": row.retry_backoff,
                "truncation_strategy": row.truncation_strategy,
                "max_doc_chars": row.max_doc_chars,
                "monitor_threshold": row.monitor_threshold,
                "provider_options": row.provider_options or {},
            }
        return {}
