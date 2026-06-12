from __future__ import annotations

from typing import Any, cast

import pytest

from forge.api.routes.v1.models import list_models


class _FakeCache:
    def __init__(self) -> None:
        self._providers = [
            {"name": "anthropic"},
            {"name": "openai"},
        ]
        self._models = {
            "anthropic": [
                {
                    "model_id": "mdl_a_1",
                    "name": "claude-sonnet-4-6",
                    "display_name": "Claude Sonnet 4.6",
                    "model_type": "chat",
                    "config": {
                        "context_window": 200000,
                        "capabilities": ["tools", "vision", "thinking"],
                        "thinking_options": ["standard", "low", "medium", "high", "xhigh"],
                    },
                },
                {
                    "model_id": "mdl_a_2",
                    "name": "anthropic-embedding-v1",
                    "display_name": "Anthropic Embedding V1",
                    "model_type": "embedding",
                    "config": {
                        "dimension": 1024,
                        "batch_size": 10,
                        "supported_dimensions": [1024],
                        "max_batch_size": 10,
                    },
                },
            ],
            "openai": [
                {
                    "model_id": "mdl_o_1",
                    "name": "gpt-4.1",
                    "display_name": "GPT-4.1",
                    "model_type": "chat",
                    "config": {
                        "context_window": 128000,
                        "capabilities": ["tools", "vision", "thinking"],
                        "thinking_options": ["standard", "low", "medium", "high", "xhigh"],
                    },
                },
            ],
        }

    async def is_ready(self) -> bool:
        return True

    async def get_providers_enabled(self) -> list[dict]:
        return self._providers

    async def get_models(self, provider_name: str, enabled_only: bool = True) -> list[dict]:
        _ = enabled_only
        return self._models.get(provider_name, [])


@pytest.mark.asyncio
async def test_list_models_grouped_by_provider() -> None:
    response = await list_models(
        db=cast(Any, None),
        model_cache=_FakeCache(),
        provider=None,
        model_type="chat",
    )
    data = response["data"]

    assert "groups" in data
    assert len(data["groups"]) == 2
    assert data["providers"] == ["anthropic", "openai"]

    anthropic_group = data["groups"][0]
    assert anthropic_group["provider"] == "anthropic"
    assert len(anthropic_group["models"]) == 1
    assert anthropic_group["models"][0]["name"] == "claude-sonnet-4-6"
    assert "thinking" in anthropic_group["models"][0]["config"]["capabilities"]
    assert anthropic_group["models"][0]["thinking"] == {
        "options": ["standard", "low", "medium", "high", "xhigh"],
        "default": "standard",
    }

    openai_group = data["groups"][1]
    assert openai_group["provider"] == "openai"
    assert openai_group["models"][0]["thinking"] == {
        "options": ["standard", "low", "medium", "high", "xhigh"],
        "default": "standard",
    }


@pytest.mark.asyncio
async def test_list_models_provider_filter() -> None:
    response = await list_models(
        db=cast(Any, None),
        model_cache=_FakeCache(),
        provider="openai",
        model_type="chat",
    )
    data = response["data"]

    assert data["provider"] == "openai"
    assert data["providers"] == ["openai"]
    assert len(data["groups"]) == 1
    assert data["groups"][0]["provider"] == "openai"
    assert data["groups"][0]["models"][0]["name"] == "gpt-4.1"
