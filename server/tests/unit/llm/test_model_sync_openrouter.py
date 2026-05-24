from __future__ import annotations

from dataclasses import dataclass

import pytest

from forge.llm import model_catalog
from forge.llm.model_catalog import (
    LocalProviderPlaceholderError,
    ModelInfo,
    ModelIntersectionEmptyError,
)
from forge.llm.model_sync_service import ModelSyncService


class _DummyCache:
    async def reload_all(self, db):  # noqa: ARG002
        return None


@dataclass
class _ProviderRow:
    id: int
    name: str
    impl: str | None
    base_url: str | None
    is_enabled: int = 1


@dataclass
class _KeyRow:
    key_ciphertext: str


class _DummyDB:
    def __init__(self) -> None:
        self.commit_called = 0
        self.rollback_called = 0

    async def commit(self) -> None:
        self.commit_called += 1

    async def rollback(self) -> None:
        self.rollback_called += 1


class _FakeModelRepo:
    def __init__(self, db) -> None:  # noqa: ARG002
        self.upserts: list[dict] = []
        self.mark_stale_called = False

    async def sync_upsert(self, provider_id: int, model_data: dict):  # noqa: ARG002
        self.upserts.append(model_data)
        return object(), len(self.upserts) == 1

    async def mark_stale(self, provider_id: int, active_names: set[str], model_type: str = "text"):  # noqa: ARG002
        self.mark_stale_called = True
        return 0


@pytest.mark.asyncio
async def test_fetch_models_cloud_path_uses_openrouter_and_provider_intersection(monkeypatch):
    class _OpenAIFetcher:
        async def fetch(self, ctx):  # noqa: ARG002
            return ["gpt-4o", "unknown-model"]

    async def _fake_openrouter_models() -> list[ModelInfo]:
        return [
            ModelInfo(
                name="openai/gpt-4o",
                provider="openrouter",
                display_name="GPT-4o",
                context_window=200000,
                max_output_tokens=8192,
                supports_tools=True,
                supports_images=True,
            )
        ]

    monkeypatch.setitem(model_catalog._FETCHERS, "openai", _OpenAIFetcher())
    monkeypatch.setattr(model_catalog, "_fetch_openrouter_models", _fake_openrouter_models)

    models = await model_catalog._fetch_models(
        {
            "name": "openai",
            "impl": "openai",
            "api_key": "sk-test",
            "base_url": None,
        }
    )

    assert len(models) == 1
    assert models[0].name == "gpt-4o"
    assert models[0].provider == "openai"
    assert models[0].context_window == 200000
    assert models[0].display_name == "GPT-4o"


@pytest.mark.asyncio
async def test_sync_provider_openrouter_failure_does_not_write_db(monkeypatch):
    db = _DummyDB()
    svc = ModelSyncService(_DummyCache())
    model_repo = _FakeModelRepo(db)

    class _ProviderRepo:
        def __init__(self, _db) -> None:
            pass

        async def get_by_name(self, name: str):  # noqa: ARG002
            return _ProviderRow(id=1, name="openai", impl="openai", base_url=None)

        async def list_enabled_keys(self, provider_id: int):  # noqa: ARG002
            return [_KeyRow(key_ciphertext="enc-key")]

    monkeypatch.setattr(
        "forge.infrastructure.database.repositories.model_provider_repo.ProviderRepository",
        _ProviderRepo,
    )
    monkeypatch.setattr(
        "forge.infrastructure.database.repositories.model_repo.ModelRepository",
        lambda _db: model_repo,
    )
    monkeypatch.setattr("forge.llm.model_sync_service.resolve_key", lambda _c: "sk-provider")

    async def _raise_openrouter(_provider):
        raise ValueError("OpenRouter 请求失败")

    monkeypatch.setattr("forge.llm.model_sync_service._fetch_models", _raise_openrouter)

    with pytest.raises(ValueError):
        await svc.sync_provider(db, "openai")

    assert db.commit_called == 0
    assert model_repo.upserts == []
    assert model_repo.mark_stale_called is False


@pytest.mark.asyncio
async def test_sync_provider_intersection_empty_returns_skip_without_write(monkeypatch):
    db = _DummyDB()
    svc = ModelSyncService(_DummyCache())
    model_repo = _FakeModelRepo(db)

    class _ProviderRepo:
        def __init__(self, _db) -> None:
            pass

        async def get_by_name(self, name: str):  # noqa: ARG002
            return _ProviderRow(id=2, name="deepseek", impl="deepseek", base_url=None)

        async def list_enabled_keys(self, provider_id: int):  # noqa: ARG002
            return [_KeyRow(key_ciphertext="enc-key")]

    monkeypatch.setattr(
        "forge.infrastructure.database.repositories.model_provider_repo.ProviderRepository",
        _ProviderRepo,
    )
    monkeypatch.setattr(
        "forge.infrastructure.database.repositories.model_repo.ModelRepository",
        lambda _db: model_repo,
    )
    monkeypatch.setattr("forge.llm.model_sync_service.resolve_key", lambda _c: "sk-provider")

    async def _raise_empty(_provider):
        raise ModelIntersectionEmptyError("deepseek")

    monkeypatch.setattr("forge.llm.model_sync_service._fetch_models", _raise_empty)

    result = await svc.sync_provider(db, "deepseek")

    assert result.models_synced == 0
    assert result.models_created == 0
    assert result.models_staled == 0
    assert result.skipped_reason == "intersection_empty"
    assert db.commit_called == 0
    assert model_repo.upserts == []
    assert model_repo.mark_stale_called is False


@pytest.mark.asyncio
async def test_sync_all_enabled_keeps_running_when_local_provider_is_placeholder(monkeypatch):
    db = _DummyDB()
    svc = ModelSyncService(_DummyCache())

    providers = [
        _ProviderRow(id=10, name="local_ollama", impl="ollama", base_url="http://127.0.0.1:11434"),
        _ProviderRow(id=11, name="openai", impl="openai", base_url=None),
    ]

    class _ProviderRepo:
        def __init__(self, _db) -> None:
            pass

        async def list_enabled(self):
            return providers

        async def get_by_name(self, name: str):
            for p in providers:
                if p.name == name:
                    return p
            return None

        async def list_enabled_keys(self, provider_id: int):  # noqa: ARG002
            return [_KeyRow(key_ciphertext="enc-key")]

    class _ModelRepo:
        def __init__(self, _db) -> None:
            self._count = 0

        async def sync_upsert(self, provider_id: int, model_data: dict):  # noqa: ARG002
            self._count += 1
            return object(), self._count == 1

        async def mark_stale(self, provider_id: int, active_names: set[str], model_type: str = "text"):  # noqa: ARG002
            return 0

    async def _fake_fetch(provider_cfg: dict):
        if provider_cfg["name"] == "local_ollama":
            raise LocalProviderPlaceholderError("local_ollama")
        return [
            ModelInfo(
                name="gpt-4o",
                provider="openai",
                display_name="GPT-4o",
                context_window=128000,
                max_output_tokens=4096,
                supports_tools=True,
                supports_images=True,
            )
        ]

    monkeypatch.setattr(
        "forge.infrastructure.database.repositories.model_provider_repo.ProviderRepository",
        _ProviderRepo,
    )
    monkeypatch.setattr(
        "forge.infrastructure.database.repositories.model_repo.ModelRepository",
        _ModelRepo,
    )
    monkeypatch.setattr("forge.llm.model_sync_service.resolve_key", lambda _c: "sk-provider")
    monkeypatch.setattr("forge.llm.model_sync_service._fetch_models", _fake_fetch)

    results = await svc.sync_all_enabled(db)

    assert len(results) == 2
    skipped = {r.provider: r for r in results}["local_ollama"]
    success = {r.provider: r for r in results}["openai"]
    assert skipped.skipped_reason == "local_placeholder"
    assert success.models_synced == 1


@pytest.mark.asyncio
async def test_fetch_models_local_provider_by_base_url_raises_placeholder(monkeypatch):
    class _NeverCalledFetcher:
        async def fetch(self, ctx):  # pragma: no cover
            raise AssertionError("本地 Provider 分支应在 fetcher 前返回")

    async def _never_called_openrouter():  # pragma: no cover
        raise AssertionError("本地 Provider 分支应跳过 OpenRouter")

    monkeypatch.setitem(model_catalog._FETCHERS, "local_ollama", _NeverCalledFetcher())
    monkeypatch.setattr(model_catalog, "_fetch_openrouter_models", _never_called_openrouter)

    with pytest.raises(LocalProviderPlaceholderError):
        await model_catalog._fetch_models(
            {
                "name": "local_ollama",
                "impl": "ollama",
                "api_key": "",
                "base_url": "http://127.0.0.1:11434",
            }
        )
