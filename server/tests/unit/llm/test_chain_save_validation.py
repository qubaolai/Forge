"""ModelChainService 保存校验单测 — 存在性 / 启用 / chat 类型 / 对话链同 provider。"""

from __future__ import annotations

import pytest
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


async def _seed(db):
    from forge.infrastructure.database.orm.model_orm import ModelOrm
    from forge.infrastructure.database.orm.model_provider_orm import ProviderOrm

    db.add_all([
        ProviderOrm(id=1001, name="mock", impl="mock", is_enabled=1),
        ProviderOrm(id=1002, name="off-prov", impl="mock", is_enabled=0),
        ModelOrm(id=2001, provider_id=1001, name="chat-a", model_type="chat", is_enabled=True),
        ModelOrm(id=2002, provider_id=1001, name="embed-a", model_type="embedding", is_enabled=True),
        ModelOrm(id=2003, provider_id=1001, name="chat-off", model_type="chat", is_enabled=False),
    ])
    await db.flush()


class _NoopCache:
    async def reload_all(self, db):  # noqa: D401
        return None


def _service(db):
    from forge.api.services.model_chain_service import ModelChainService
    return ModelChainService(db, model_cache=_NoopCache())


async def test_valid_tier_chain_saved_and_deduped(factory):
    async with factory() as db:
        await _seed(db)
        result = await _service(db).set_chain(
            "tier", "fast",
            [
                {"provider": "mock", "model": "chat-a"},
                {"provider": "mock", "model": "chat-a"},  # 重复 → 去重
            ],
        )
        assert result["entries"] == [{"provider": "mock", "model": "chat-a"}]
        assert result["version"] == 1


async def test_reject_unknown_provider(factory):
    async with factory() as db:
        await _seed(db)
        with pytest.raises(ValueError, match="供应商不存在或未启用"):
            await _service(db).set_chain("tier", "fast", [{"provider": "nope", "model": "x"}])


async def test_reject_disabled_provider(factory):
    async with factory() as db:
        await _seed(db)
        with pytest.raises(ValueError, match="供应商不存在或未启用"):
            await _service(db).set_chain("tier", "fast", [{"provider": "off-prov", "model": "chat-a"}])


async def test_reject_non_chat_model(factory):
    async with factory() as db:
        await _seed(db)
        with pytest.raises(ValueError, match="模型不存在/未启用/非chat"):
            await _service(db).set_chain("tier", "fast", [{"provider": "mock", "model": "embed-a"}])


async def test_reject_disabled_model(factory):
    async with factory() as db:
        await _seed(db)
        with pytest.raises(ValueError, match="模型不存在/未启用/非chat"):
            await _service(db).set_chain("tier", "fast", [{"provider": "mock", "model": "chat-off"}])


async def test_reject_conversation_entry_cross_provider(factory):
    async with factory() as db:
        await _seed(db)
        with pytest.raises(ValueError, match="必须等于 mock"):
            await _service(db).set_chain(
                "conversation", "mock", [{"provider": "other", "model": "chat-a"}]
            )


async def test_reject_unknown_tier(factory):
    async with factory() as db:
        await _seed(db)
        with pytest.raises(ValueError, match="未知档位"):
            await _service(db).set_chain("tier", "ultra", [{"provider": "mock", "model": "chat-a"}])
