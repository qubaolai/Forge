"""ChainResolver 单测 — 覆盖 user_pin(同 provider)/ 档位链(跨 provider)/ 能力过滤。

用 FakeCache 注入模型详情与链配置,纯逻辑、不碰 DB / Redis。
"""

from __future__ import annotations

import asyncio

import pytest

from forge.llm.dispatch.chain_resolver import resolve_chain
from forge.llm.request import LLMRequest


def _detail(model: str, *, enabled: bool = True, caps: list[str] | None = None,
            context_window: int = 128000, modalities: list[str] | None = None) -> dict:
    return {
        "name": model,
        "model_type": "chat",
        "is_enabled": enabled,
        "config": {
            "capabilities": caps if caps is not None else ["tools"],
            "context_window": context_window,
            "input_modalities": modalities or ["text"],
        },
    }


class _FakeCache:
    def __init__(self, models: dict[tuple[str, str], dict], chains: dict[tuple[str, str], list]):
        self._models = models
        self._chains = chains

    async def get_model_detail(self, provider: str, model: str) -> dict | None:
        return self._models.get((provider, model))

    async def get_chain(self, scope: str, chain_key: str) -> list[dict]:
        return list(self._chains.get((scope, chain_key), []))


class _Settings:
    def __init__(self, provider: str = "", default_model: str | None = None):
        self.llm = type("L", (), {"provider": provider, "default_model": default_model})()


def _run(req, settings, cache):
    return asyncio.run(resolve_chain(req, settings, cache))


# ---- user_pin(web):同 provider 后备,绝不跨厂商 ----

def test_user_pin_head_plus_same_provider_backup():
    cache = _FakeCache(
        models={
            ("openai", "gpt-a"): _detail("gpt-a"),
            ("openai", "gpt-b"): _detail("gpt-b"),
            ("anthropic", "claude"): _detail("claude"),
        },
        chains={
            # 对话链含跨厂商项, resolver 必须把跨厂商的滤掉
            ("conversation", "openai"): [
                {"provider": "openai", "model": "gpt-b"},
                {"provider": "anthropic", "model": "claude"},
            ],
        },
    )
    req = LLMRequest(messages=[], preferred_provider="openai", preferred_model="gpt-a")
    chain = _run(req, _Settings(), cache)
    # 链头是 pin, 后备只含同 provider 的 gpt-b, 跨厂商 claude 被排除
    assert chain == [("openai", "gpt-a"), ("openai", "gpt-b")]


def test_user_pin_fail_fast_when_capability_unmet():
    cache = _FakeCache(
        models={("openai", "gpt-a"): _detail("gpt-a", caps=[])},  # 无 tools 能力
        chains={},
    )
    req = LLMRequest(messages=[], preferred_provider="openai", preferred_model="gpt-a", tools=[{"x": 1}])
    with pytest.raises(ValueError, match="不满足本次调用能力"):
        _run(req, _Settings(), cache)


def test_user_pin_fail_fast_when_disabled():
    cache = _FakeCache(
        models={("openai", "gpt-a"): _detail("gpt-a", enabled=False)},
        chains={},
    )
    req = LLMRequest(messages=[], preferred_provider="openai", preferred_model="gpt-a")
    with pytest.raises(ValueError, match="不存在/未启用"):
        _run(req, _Settings(), cache)


# ---- 档位链(CLI/utility):允许跨 provider,保配置顺序 ----

def test_tier_chain_cross_provider_preserves_order():
    cache = _FakeCache(
        models={
            ("dash", "qwen"): _detail("qwen"),
            ("anthropic", "claude"): _detail("claude"),
        },
        chains={
            ("tier", "strong"): [
                {"provider": "dash", "model": "qwen"},
                {"provider": "anthropic", "model": "claude"},
            ],
        },
    )
    req = LLMRequest(messages=[], model_profile="strong")
    chain = _run(req, _Settings(), cache)
    assert chain == [("dash", "qwen"), ("anthropic", "claude")]


def test_tier_chain_filters_capability_and_disabled():
    cache = _FakeCache(
        models={
            ("dash", "qwen"): _detail("qwen", caps=["tools"]),
            ("dash", "qwen-lite"): _detail("qwen-lite", caps=[]),       # 无 tools → 滤掉
            ("dash", "qwen-off"): _detail("qwen-off", enabled=False),   # 禁用 → 滤掉
        },
        chains={
            ("tier", "fast"): [
                {"provider": "dash", "model": "qwen-lite"},
                {"provider": "dash", "model": "qwen-off"},
                {"provider": "dash", "model": "qwen"},
            ],
        },
    )
    req = LLMRequest(messages=[], model_profile="fast", requires_tools=True)
    chain = _run(req, _Settings(), cache)
    assert chain == [("dash", "qwen")]


def test_tier_empty_falls_back_to_default():
    cache = _FakeCache(
        models={("dash", "qwen"): _detail("qwen")},
        chains={("tier", "fast"): []},
    )
    req = LLMRequest(messages=[], model_profile="fast")
    chain = _run(req, _Settings(provider="dash", default_model="qwen"), cache)
    assert chain == [("dash", "qwen")]


def test_no_pin_no_tier_no_default_raises():
    cache = _FakeCache(models={}, chains={})
    req = LLMRequest(messages=[])
    with pytest.raises(ValueError, match="无法解析模型调用链"):
        _run(req, _Settings(), cache)


def test_vision_requirement_filters_text_only_model():
    cache = _FakeCache(
        models={
            ("p", "text-only"): _detail("text-only", caps=["tools"], modalities=["text"]),
            ("p", "vlm"): _detail("vlm", caps=["tools", "vision"], modalities=["text", "image"]),
        },
        chains={
            ("tier", "smart"): [
                {"provider": "p", "model": "text-only"},
                {"provider": "p", "model": "vlm"},
            ],
        },
    )
    req = LLMRequest(messages=[], model_profile="smart", requires_vision=True)
    chain = _run(req, _Settings(), cache)
    assert chain == [("p", "vlm")]
