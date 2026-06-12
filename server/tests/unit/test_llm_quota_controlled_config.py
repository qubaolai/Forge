from __future__ import annotations

from typing import Any, cast

from forge.config.domains.llm import LLMConfig


def test_provider_quota_controlled_flows_into_call_spec() -> None:
    cfg = LLMConfig(
        provider="server_openai",
        default_model="gpt-4o",
        providers=cast(Any, {
            "server_openai": {
                "impl": "openai",
                "quota_controlled": True,
                "models": [{"name": "gpt-4o"}],
            },
            "user_openai": {
                "impl": "openai",
                "models": [{"name": "gpt-4o"}],
            },
        }),
    )

    assert cfg.resolve("server_openai", "gpt-4o").quota_controlled is True
    assert cfg.resolve("user_openai", "gpt-4o").quota_controlled is False


def test_model_quota_controlled_overrides_provider_default() -> None:
    cfg = LLMConfig(
        provider="mixed",
        providers=cast(Any, {
            "mixed": {
                "impl": "openai",
                "quota_controlled": True,
                "models": [
                    {"name": "server-model"},
                    {"name": "user-pass-through", "quota_controlled": False},
                ],
            },
        }),
    )

    assert cfg.resolve("mixed", "server-model").quota_controlled is True
    assert cfg.resolve("mixed", "user-pass-through").quota_controlled is False
