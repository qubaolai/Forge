from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


@pytest.fixture
async def factory():
    import forge.infrastructure.database.orm  # noqa: F401
    from forge.infrastructure.database.orm.base import Base

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed_models(db):
    from forge.infrastructure.database.orm.model_orm import ModelOrm
    from forge.infrastructure.database.orm.model_provider_orm import ProviderOrm
    from forge.infrastructure.database.repositories.model_config_repo import ModelConfigRepository

    provider = ProviderOrm(id=1001, name="mock", impl="mock", is_enabled=1)
    embedding = ModelOrm(
        id=2001,
        provider_id=provider.id,
        name="embed-a",
        display_name="Embed A",
        model_type="embedding",
        is_enabled=True,
    )
    reranker = ModelOrm(
        id=2002,
        provider_id=provider.id,
        name="rerank-a",
        display_name="Rerank A",
        model_type="reranker",
        is_enabled=True,
    )
    chat = ModelOrm(
        id=2003,
        provider_id=provider.id,
        name="chat-a",
        display_name="Chat A",
        model_type="chat",
        is_enabled=True,
    )
    db.add_all([provider, embedding, reranker, chat])
    await db.flush()
    configs = ModelConfigRepository(db)
    embedding_config = await configs.create(embedding.id, "embedding", {
        "dimension": 8,
        "batch_size": 2,
        "input_modalities": ["text"],
        "max_retries": 1,
        "retry_backoff": 0.1,
        "provider_options": {},
    })
    await configs.create(reranker.id, "reranker", {
        "timeout_seconds": 1.0,
        "max_retries": 1,
        "retry_backoff": 0.1,
        "truncation_strategy": "tail",
        "max_doc_chars": 1000,
        "monitor_threshold": 0.1,
        "provider_options": {},
    })
    await configs.create(chat.id, "chat", {
        "context_window": 8192,
        "max_output_tokens": 1024,
        "input_modalities": ["text"],
        "output_modalities": ["text"],
        "capabilities": ["tools"],
        "thinking_options": None,
        "provider_options": {},
    })
    return provider, embedding, reranker, chat, embedding_config


@pytest.mark.asyncio
async def test_model_config_uses_independent_snowflake_id_and_unique_model_id(factory):
    from forge.infrastructure.database.orm.model_config_orm import EmbeddingModelConfigOrm
    from forge.infrastructure.database.repositories.model_config_repo import ModelConfigRepository

    async with factory() as db:
        _, embedding, _, chat, config = await _seed_models(db)
        await db.commit()

        assert isinstance(config.id, int)
        assert config.id != embedding.id
        with pytest.raises(ValueError, match="不能写入 embedding 配置"):
            await ModelConfigRepository(db).create(chat.id, "embedding", {"dimension": 16})

        db.add(EmbeddingModelConfigOrm(model_id=embedding.id, dimension=16))
        with pytest.raises(IntegrityError):
            await db.flush()


@pytest.mark.asyncio
async def test_rag_embedding_binding_marks_vectors_stale_and_is_idempotent(factory):
    from forge.api.services.admin_model_service import AdminModelService
    from forge.api.services.system_model_binding_service import SystemModelBindingService
    from forge.infrastructure.database.orm.kb_document_orm import KbDocumentOrm

    async with factory() as db:
        _, embedding, _, _, _ = await _seed_models(db)
        doc = KbDocumentOrm(
            id=3001,
            kb_id=4001,
            name="doc.txt",
            mime_type="text/plain",
            size_bytes=10,
            status="indexed",
            vector_index_status="ready",
            embedding_model_id=embedding.id,
        )
        db.add(doc)
        await db.flush()

        service = SystemModelBindingService(db)
        first = await service.set_binding("rag_embedding", str(embedding.id))
        assert first["version"] == 1
        assert doc.vector_index_status == "stale"

        doc.vector_index_status = "ready"
        second = await service.set_binding("rag_embedding", str(embedding.id))
        assert second["version"] == 1
        assert doc.vector_index_status == "ready"

        await service.set_binding("semantic_history_embedding", str(embedding.id))
        with pytest.raises(ValueError, match="正在被系统角色"):
            await AdminModelService(db, model_cache=object())._ensure_model_not_bound(
                embedding.id, action="禁用"
            )


@pytest.mark.asyncio
async def test_binding_validates_type_and_reranker_does_not_stale_vectors(factory):
    from forge.api.services.system_model_binding_service import SystemModelBindingService
    from forge.infrastructure.database.orm.kb_document_orm import KbDocumentOrm

    async with factory() as db:
        provider, _, reranker, chat, _ = await _seed_models(db)
        doc = KbDocumentOrm(
            id=3001,
            kb_id=4001,
            name="doc.txt",
            mime_type="text/plain",
            size_bytes=10,
            status="indexed",
            vector_index_status="ready",
        )
        db.add(doc)
        await db.flush()

        service = SystemModelBindingService(db)
        with pytest.raises(ValueError, match="只能绑定 embedding"):
            await service.set_binding("rag_embedding", str(chat.id))

        await service.set_binding("rag_reranker", str(reranker.id))
        assert doc.vector_index_status == "ready"

        provider.is_enabled = 0
        await db.flush()
        with pytest.raises(ValueError, match="供应商不存在或未启用"):
            await service.set_binding("semantic_history_embedding", "2001")


@pytest.mark.asyncio
async def test_legacy_text_model_migrates_to_chat_config(factory):
    from sqlalchemy import select

    from forge.infrastructure.database.database import _migrate_model_configs
    from forge.infrastructure.database.orm.model_config_orm import ChatModelConfigOrm
    from forge.infrastructure.database.orm.model_orm import ModelOrm
    from forge.infrastructure.database.orm.model_provider_orm import ProviderOrm
    from forge.infrastructure.database.orm.system_model_binding_orm import SystemModelBindingOrm

    async with factory() as db:
        db.add(ProviderOrm(id=1001, name="mock", impl="mock", is_enabled=1))
        db.add(ModelOrm(
            id=2001,
            provider_id=1001,
            name="legacy-chat",
            display_name="Legacy Chat",
            model_type="text",
            context_window=32000,
            max_output_tokens=2048,
            supports_tools=True,
            supports_images=True,
            supports_thinking=False,
            extra_params={"temperature": 0.2, "vendor_flag": True},
        ))
        await db.commit()

    engine = factory.kw["bind"]
    async with engine.begin() as conn:
        await conn.run_sync(_migrate_model_configs)

    async with factory() as db:
        model = await db.get(ModelOrm, 2001)
        config = (await db.execute(
            select(ChatModelConfigOrm).where(ChatModelConfigOrm.model_id == model.id)
        )).scalar_one()
        roles = set((await db.execute(select(SystemModelBindingOrm.role))).scalars())

        assert model.model_type == "chat"
        assert config.context_window == 32000
        assert config.max_output_tokens == 2048
        assert config.input_modalities == ["text", "image"]
        assert config.provider_options == {"temperature": 0.2, "vendor_flag": True}
        assert roles == {"rag_embedding", "semantic_history_embedding", "rag_reranker"}
