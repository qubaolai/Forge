from __future__ import annotations

import asyncio

import pytest

from forge.llm.dispatch.chain_builder import build_dispatch_chain as build_chain_from_settings


class _FakeCache:
    def __init__(self, *, ready: bool = True, keys: list[dict] | None = None) -> None:
        self._ready = ready
        self._keys = keys if keys is not None else [{"api_key": "sk-db", "weight": 1}]

    async def is_ready(self) -> bool:
        return self._ready

    async def is_model_enabled(self, provider: str, model: str) -> bool:
        return provider == "openai" and model == "gpt-4o"

    async def get_provider(self, provider: str) -> dict | None:
        return {"name": provider, "impl": "mock", "base_url": None}

    async def get_model_detail(self, provider: str, model: str) -> dict | None:
        return {
            "name": model,
            "max_output_tokens": 4096,
            "extra_params": {"temperature": 0.2},
        }

    async def get_keys(self, provider: str) -> list[dict]:
        return self._keys


class _FakeClient:
    provider_name = "mock"
    api_key_fingerprint = "sk-db***"
    supports_tool_calling = True


class _FakePool:
    def __init__(self) -> None:
        self.reconciled = []

    def reconcile_provider(self, impl: str, keys: list[dict], client_options: dict) -> dict:
        self.reconciled.append((impl, keys, client_options))
        return {"added": len(keys), "removed": 0}

    def get_candidates_by_impl(self, impl: str, client_options: dict):
        return [(_FakeClient(), "sk-db")]


class _Settings:
    class llm:
        max_retries = 0
        retry_backoff_seconds = 0.0


def test_build_chain_requires_ready_cache(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")

    with pytest.raises(ValueError, match="模型配置缓存未就绪"):
        asyncio.run(
            build_chain_from_settings(
                _Settings(),
                provider="openai",
                model="gpt-4o",
                model_cache=_FakeCache(ready=False),
            )
        )


def test_build_chain_uses_cache_key_and_pool(monkeypatch) -> None:
    pool = _FakePool()
    monkeypatch.setattr("forge.llm.client_pool.get_llm_pool", lambda: pool)

    chain = asyncio.run(
        build_chain_from_settings(
            _Settings(),
            provider="openai",
            model="gpt-4o",
            model_cache=_FakeCache(keys=[{"api_key": "sk-db", "weight": 2}]),
        )
    )

    assert chain.primary_spec.api_key == "sk-db"
    assert chain.primary_spec.provider_name == "openai"
    assert chain.primary_spec.impl == "mock"
    assert chain.primary_spec.temperature == 0.2
    assert pool.reconciled[0][1] == [{"api_key": "sk-db", "weight": 2}]
