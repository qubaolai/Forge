from __future__ import annotations

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
                    "model_type": "text",
                    "context_window": 200000,
                    "supports_tools": True,
                    "supports_images": True,
                    "supports_thinking": True,
                    "thinking_type": "enabled",
                    "thinking_options": None,
                    "thinking_default": "enabled",
                },
                {
                    "model_id": "mdl_a_2",
                    "name": "anthropic-embedding-v1",
                    "display_name": "Anthropic Embedding V1",
                    "model_type": "embedding",
                    "context_window": 8192,
                    "supports_tools": False,
                    "supports_images": False,
                    "supports_thinking": False,
                    "thinking_type": None,
                    "thinking_options": None,
                    "thinking_default": None,
                },
            ],
            "openai": [
                {
                    "model_id": "mdl_o_1",
                    "name": "gpt-4.1",
                    "display_name": "GPT-4.1",
                    "model_type": "text",
                    "context_window": 128000,
                    "supports_tools": True,
                    "supports_images": True,
                    "supports_thinking": True,
                    "thinking_type": "reasoning_effort",
                    "thinking_options": ["high", "max"],
                    "thinking_default": "high",
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
        db=None,
        model_cache=_FakeCache(),
        provider=None,
        model_type="text",
    )
    data = response["data"]

    assert "groups" in data
    assert len(data["groups"]) == 2
    assert data["providers"] == ["anthropic", "openai"]

    anthropic_group = data["groups"][0]
    assert anthropic_group["provider"] == "anthropic"
    assert len(anthropic_group["models"]) == 1
    assert anthropic_group["models"][0]["name"] == "claude-sonnet-4-6"
    assert anthropic_group["models"][0]["thinking"] == {"type": "enabled", "default": "enabled"}

    openai_group = data["groups"][1]
    assert openai_group["provider"] == "openai"
    assert openai_group["models"][0]["thinking"]["type"] == "reasoning_effort"


@pytest.mark.asyncio
async def test_list_models_provider_filter() -> None:
    response = await list_models(
        db=None,
        model_cache=_FakeCache(),
        provider="openai",
        model_type="text",
    )
    data = response["data"]

    assert data["provider"] == "openai"
    assert data["providers"] == ["openai"]
    assert len(data["groups"]) == 1
    assert data["groups"][0]["provider"] == "openai"
    assert data["groups"][0]["models"][0]["name"] == "gpt-4.1"
