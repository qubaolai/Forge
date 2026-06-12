from __future__ import annotations

import asyncio

import pytest

from forge.llm.dispatch.chain_builder import build_dispatch_chain as build_chain_from_settings


class _FakeCache:
    def __init__(
        self,
        *,
        ready: bool = True,
        keys: list[dict] | None = None,
        model_type: str = "chat",
    ) -> None:
        self._ready = ready
        self._keys = keys if keys is not None else [{"api_key": "sk-db", "weight": 1}]
        self._model_type = model_type

    async def is_ready(self) -> bool:
        return self._ready

    async def is_model_enabled(self, provider: str, model: str) -> bool:
        return provider == "openai" and model == "gpt-4o"

    async def get_provider(self, provider: str) -> dict | None:
        return {"name": provider, "impl": "mock", "base_url": None}

    async def get_model_detail(self, provider: str, model: str) -> dict | None:
        return {
            "name": model,
            "model_type": self._model_type,
            "config": {
                "max_output_tokens": 4096,
                "provider_options": {
                    "temperature": 0.2,
                    "max_tokens": 2048,
                    "top_p": 0.9,
                    "vendor_flag": "enabled",
                },
            },
        }

    async def get_keys(self, provider: str) -> list[dict]:
        return self._keys


class _FakeClient:
    provider_name = "mock"
    api_key_fingerprint = "sk-db***"
    supports_tool_calling = True


class _FakePool:
    def __init__(self) -> None:
        self.reconciled: list[tuple[str, list[dict], dict]] = []

    def reconcile_provider(self, impl: str, keys: list[dict], client_options: dict) -> dict:
        self.reconciled.append((impl, keys, client_options))
        return {"added": len(keys), "removed": 0}

    def get_candidates_by_impl(self, impl: str, client_options: dict):
        return [(_FakeClient(), "sk-db")]


class _Settings:
    class Llm:
        max_retries = 0
        retry_backoff_seconds = 0.0

    llm = Llm()


def test_build_chain_requires_ready_cache(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")

    # 单模型链且缓存未就绪 → 该条目展开失败被收敛为构链失败
    with pytest.raises(ValueError, match="LLM 调用链构建失败"):
        asyncio.run(
            build_chain_from_settings(
                _Settings(),
                chain=[("openai", "gpt-4o")],
                model_cache=_FakeCache(ready=False),
            )
        )


def test_build_chain_uses_cache_key_and_pool(monkeypatch) -> None:
    pool = _FakePool()
    monkeypatch.setattr("forge.llm.client_pool.get_llm_pool", lambda: pool)

    chain = asyncio.run(
        build_chain_from_settings(
            _Settings(),
            chain=[("openai", "gpt-4o")],
            model_cache=_FakeCache(keys=[{"api_key": "sk-db", "weight": 2}]),
        )
    )

    assert chain.primary_spec.api_key == "sk-db"
    assert chain.primary_spec.provider_name == "openai"
    assert chain.primary_spec.impl == "mock"
    assert chain.primary_spec.temperature == 0.2
    assert chain.primary_spec.max_tokens == 2048
    assert chain.primary_spec.top_p == 0.9
    assert chain.primary_spec.extra == {"vendor_flag": "enabled"}
    assert pool.reconciled[0][1] == [{"api_key": "sk-db", "weight": 2}]


def test_build_chain_rejects_non_chat_model() -> None:
    # 非 chat 模型由 _build_entries_from_cache 拒绝, 被构链层收敛为构链失败
    with pytest.raises(ValueError, match="LLM 调用链构建失败"):
        asyncio.run(
            build_chain_from_settings(
                _Settings(),
                chain=[("openai", "gpt-4o")],
                model_cache=_FakeCache(model_type="embedding"),
            )
        )
