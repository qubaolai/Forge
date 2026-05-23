"""模型目录与供应商模型抓取注册中心。"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx

logger = logging.getLogger(__name__)


@dataclass
class ThinkingMeta:
    type: Literal["reasoning_effort", "enabled"]
    options: list[str] | None = None
    default: str | None = None


@dataclass
class ModelInfo:
    name: str
    provider: str
    display_name: str = ""
    context_window: int = 128000
    max_output_tokens: int = 4096
    supports_tools: bool = True
    supports_images: bool = False
    thinking: ThinkingMeta | None = None
    model_type: str = "text"
    extra_params: dict | None = None


@dataclass(frozen=True)
class ProviderFetchContext:
    provider_name: str
    api_key: str
    base_url: str | None
    impl: str | None


@dataclass(frozen=True)
class ModelCapability:
    context_window: int = 128000
    max_output_tokens: int = 4096
    supports_tools: bool = True
    supports_images: bool = False
    thinking: ThinkingMeta | None = None
    model_type: str = "text"
    display_name: str = ""
    extra_params: dict | None = None


class ProviderModelFetcher(Protocol):
    async def fetch(self, ctx: ProviderFetchContext) -> list[str]:
        """返回供应商可用模型 ID 列表。"""


_FETCHERS: dict[str, ProviderModelFetcher] = {}
_CAPABILITIES: dict[str, ModelCapability] = {}


def register_model_fetcher(provider_name: str, fetcher: ProviderModelFetcher) -> None:
    normalized = provider_name.strip().lower()
    if not normalized:
        raise ValueError("provider_name 不能为空")
    _FETCHERS[normalized] = fetcher


def register_model_capability(model_name: str, capability: ModelCapability) -> None:
    _CAPABILITIES[model_name] = capability


class _FixedListFetcher:
    def __init__(self, model_ids: list[str]) -> None:
        self._model_ids = list(model_ids)

    async def fetch(self, ctx: ProviderFetchContext) -> list[str]:  # noqa: ARG002
        return list(self._model_ids)


class _OpenAICompatibleFetcher:
    def __init__(self, default_base_url: str) -> None:
        self._default_base_url = default_base_url.rstrip("/")

    async def fetch(self, ctx: ProviderFetchContext) -> list[str]:
        if not ctx.api_key:
            raise ValueError(f"{ctx.provider_name}: 缺少 API Key")

        base_url = (ctx.base_url or self._default_base_url).rstrip("/")
        url = f"{base_url}/v1/models"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {ctx.api_key}"})
            resp.raise_for_status()
            data = resp.json()
        items = data.get("data", [])
        return [item["id"] for item in items if "id" in item]


class ModelCatalog:
    """进程内模型目录。"""

    def __init__(self) -> None:
        self._models: dict[str, list[ModelInfo]] = {}

    @property
    def models(self) -> dict[str, list[ModelInfo]]:
        return self._models

    def get_models(self, provider: str) -> list[ModelInfo]:
        return self._models.get(provider, [])

    def list_providers(self) -> list[str]:
        return sorted(self._models.keys())

    async def refresh(self, providers: list[dict]) -> None:
        new_models: dict[str, list[ModelInfo]] = {}
        for provider in providers:
            provider_name = provider["name"]
            try:
                models = await _fetch_models(provider)
                new_models[provider_name] = models
                logger.info("模型目录刷新完成 provider=%s count=%d", provider_name, len(models))
            except Exception:
                logger.exception("模型目录刷新失败 provider=%s", provider_name)
                raise
        self._models = new_models


def _build_fetch_context(provider: dict) -> ProviderFetchContext:
    provider_name = provider["name"]
    api_key = provider.get("api_key") or os.environ.get(f"{provider_name.upper()}_API_KEY", "")
    return ProviderFetchContext(
        provider_name=provider_name,
        api_key=api_key,
        base_url=provider.get("base_url"),
        impl=provider.get("impl"),
    )


def _capability_to_model_info(provider_name: str, model_id: str) -> ModelInfo:
    cap = _CAPABILITIES.get(model_id, ModelCapability())
    return ModelInfo(
        name=model_id,
        provider=provider_name,
        display_name=cap.display_name or model_id,
        context_window=cap.context_window,
        max_output_tokens=cap.max_output_tokens,
        supports_tools=cap.supports_tools,
        supports_images=cap.supports_images,
        thinking=cap.thinking,
        model_type=cap.model_type,
        extra_params=cap.extra_params,
    )


async def _fetch_models(provider: dict) -> list[ModelInfo]:
    """兼容旧调用入口：按 provider 名调对应 fetcher，再附加能力元数据。"""
    provider_name = provider["name"].strip().lower()
    fetcher = _FETCHERS.get(provider_name)
    if fetcher is None:
        raise ValueError(f"不支持的供应商: {provider_name}")

    ctx = _build_fetch_context(provider)
    model_ids = await fetcher.fetch(ctx)
    return [_capability_to_model_info(provider_name, model_id) for model_id in model_ids]


def _register_builtin_fetchers() -> None:
    register_model_fetcher(
        "anthropic",
        _FixedListFetcher(
            [
                "claude-sonnet-4-6",
                "claude-sonnet-4-5",
                "claude-haiku-4-5",
                "claude-opus-4-7",
            ]
        ),
    )
    register_model_fetcher("openai", _OpenAICompatibleFetcher("https://api.openai.com"))
    register_model_fetcher("deepseek", _OpenAICompatibleFetcher("https://api.deepseek.com"))
    register_model_fetcher(
        "dashscope",
        _OpenAICompatibleFetcher("https://dashscope.aliyuncs.com/compatible-mode"),
    )


def _register_builtin_capabilities() -> None:
    register_model_capability(
        "claude-sonnet-4-6",
        ModelCapability(
            context_window=200000,
            supports_tools=True,
            supports_images=True,
            thinking=ThinkingMeta(type="enabled"),
        ),
    )
    register_model_capability(
        "claude-sonnet-4-5",
        ModelCapability(
            context_window=200000,
            supports_tools=True,
            supports_images=True,
            thinking=ThinkingMeta(type="enabled"),
        ),
    )
    register_model_capability(
        "claude-haiku-4-5",
        ModelCapability(
            context_window=200000,
            supports_tools=True,
            supports_images=True,
            thinking=ThinkingMeta(type="enabled"),
        ),
    )
    register_model_capability(
        "claude-opus-4-7",
        ModelCapability(
            context_window=200000,
            supports_tools=True,
            supports_images=True,
            thinking=ThinkingMeta(type="enabled"),
        ),
    )

    register_model_capability(
        "gpt-4o",
        ModelCapability(context_window=128000, supports_tools=True, supports_images=True),
    )
    register_model_capability(
        "gpt-4o-mini",
        ModelCapability(context_window=128000, supports_tools=True, supports_images=True),
    )

    register_model_capability(
        "deepseek-v4-pro",
        ModelCapability(
            context_window=1000000,
            max_output_tokens=32768,
            supports_tools=True,
            supports_images=False,
            thinking=ThinkingMeta(type="reasoning_effort", options=["high", "max"], default="high"),
        ),
    )
    register_model_capability(
        "deepseek-v4-flash",
        ModelCapability(
            context_window=1000000,
            max_output_tokens=32768,
            supports_tools=True,
            supports_images=False,
            thinking=ThinkingMeta(type="reasoning_effort", options=["high", "max"], default="high"),
        ),
    )

    register_model_capability(
        "qwen3-max-preview",
        ModelCapability(context_window=32768, supports_tools=True, supports_images=False),
    )
    register_model_capability(
        "qwen-plus",
        ModelCapability(context_window=131072, supports_tools=True, supports_images=False),
    )
    register_model_capability(
        "qwen3.6-plus",
        ModelCapability(context_window=131072, supports_tools=True, supports_images=False),
    )
    register_model_capability(
        "qwen3.5-flash",
        ModelCapability(context_window=8192, supports_tools=True, supports_images=False),
    )
    register_model_capability(
        "qwen3.7-max",
        ModelCapability(context_window=131072, supports_tools=True, supports_images=False),
    )


_catalog: ModelCatalog | None = None


def get_model_catalog() -> ModelCatalog:
    if _catalog is None:
        raise RuntimeError("ModelCatalog 尚未初始化, 请确认 lifespan 已启动")
    return _catalog


def init_model_catalog() -> ModelCatalog:
    global _catalog
    _catalog = ModelCatalog()
    return _catalog


_register_builtin_fetchers()
_register_builtin_capabilities()

