"""模型目录与供应商模型抓取注册中心。"""

from __future__ import annotations

import ipaddress
import logging
import os
from dataclasses import dataclass
from typing import Literal, Protocol
from urllib.parse import urlparse

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
_OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1/models").rstrip("/")


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


class LocalProviderPlaceholderError(RuntimeError):
    """本地 Provider 占位同步异常（仅跳过，不落库）。"""

    def __init__(self, provider_name: str, reason: str = "local_placeholder") -> None:
        super().__init__(f"{provider_name}: 本地 Provider 占位逻辑，暂不执行模型同步")
        self.provider_name = provider_name
        self.reason = reason


class ModelIntersectionEmptyError(RuntimeError):
    """OpenRouter 与 Provider 交集为空。"""

    def __init__(self, provider_name: str) -> None:
        super().__init__(f"{provider_name}: OpenRouter 与 Provider 返回模型交集为空")
        self.provider_name = provider_name


def _normalize_model_name(name: str) -> str:
    return (name or "").strip().lower()


def _model_match_keys(model_name: str) -> set[str]:
    normalized = _normalize_model_name(model_name)
    if not normalized:
        return set()
    keys = {normalized}
    if "/" in normalized:
        # OpenRouter 常见 model id 形如 provider/model，此处仅补一个稳定拆分键。
        keys.add(normalized.split("/", 1)[1])
    return keys


def _openrouter_auth_header() -> dict[str, str]:
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise ValueError("缺少 OPENROUTER_API_KEY，严格模式下无法执行云 Provider 同步")
    return {"Authorization": f"Bearer {api_key}"}


def _to_int(value: object, default: int) -> int:
    try:
        iv = int(value)  # type: ignore[arg-type]
        return iv if iv > 0 else default
    except Exception:
        return default


def _extract_openrouter_max_output(item: dict) -> int:
    top = item.get("top_provider") or {}
    if isinstance(top, dict):
        for key in ("max_completion_tokens", "max_output_tokens"):
            if key in top:
                return _to_int(top.get(key), 4096)
    return _to_int(item.get("max_output_tokens"), 4096)


def _supports_tools_from_openrouter(item: dict) -> bool:
    params = item.get("supported_parameters") or []
    if not isinstance(params, list):
        return True
    normalized = {_normalize_model_name(str(p)) for p in params}
    tool_keys = {"tools", "tool_choice", "function_call", "functions"}
    return bool(normalized & tool_keys)


def _supports_images_from_openrouter(item: dict) -> bool:
    arch = item.get("architecture") or {}
    if not isinstance(arch, dict):
        return False
    modalities = arch.get("input_modalities") or []
    if not isinstance(modalities, list):
        return False
    normalized = {_normalize_model_name(str(m)) for m in modalities}
    return "image" in normalized


def _parse_openrouter_model(item: dict) -> ModelInfo | None:
    model_id = str(item.get("id") or "").strip()
    if not model_id:
        return None
    return ModelInfo(
        name=model_id,
        provider="openrouter",
        display_name=str(item.get("name") or model_id),
        context_window=_to_int(item.get("context_length"), 128000),
        max_output_tokens=_extract_openrouter_max_output(item),
        supports_tools=_supports_tools_from_openrouter(item),
        supports_images=_supports_images_from_openrouter(item),
        thinking=None,
        model_type="text",
        extra_params={"source": "openrouter"},
    )


async def _fetch_openrouter_models() -> list[ModelInfo]:
    url = f"{_OPENROUTER_BASE_URL}"
    # headers = _openrouter_auth_header()
    logger.info("开始请求 OpenRouter 模型目录 url=%s", url)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            # resp = await client.get(url, headers=headers)
            resp = await client.get(url)
            resp.raise_for_status()
            payload = resp.json()
    except Exception:
        logger.exception("请求 OpenRouter 模型目录失败 url=%s", url)
        raise
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    models: list[ModelInfo] = []
    for row in rows:
        print(row)
        if not isinstance(row, dict):
            continue
        parsed = _parse_openrouter_model(row)
        if parsed is not None:
            models.append(parsed)
    logger.info("OpenRouter 模型目录请求成功 count=%d", len(models))
    return models


def _build_openrouter_index(rows: list[ModelInfo]) -> dict[str, ModelInfo]:
    indexed: dict[str, ModelInfo] = {}
    for row in rows:
        for key in _model_match_keys(row.name):
            indexed.setdefault(key, row)
    return indexed


def _clone_from_openrouter(openrouter_model: ModelInfo, provider_name: str, provider_model_id: str) -> ModelInfo:
    cap = _CAPABILITIES.get(provider_model_id)
    context_window = openrouter_model.context_window
    max_output_tokens = openrouter_model.max_output_tokens
    supports_tools = openrouter_model.supports_tools
    supports_images = openrouter_model.supports_images
    thinking = openrouter_model.thinking

    # OpenRouter 为主数据源，仅在缺字段时使用本地能力作为兜底。
    if cap is not None:
        if context_window <= 0:
            context_window = cap.context_window
        if max_output_tokens <= 0:
            max_output_tokens = cap.max_output_tokens
        if thinking is None:
            thinking = cap.thinking

    extra = dict(openrouter_model.extra_params or {})
    extra["openrouter_id"] = openrouter_model.name
    return ModelInfo(
        name=provider_model_id,
        provider=provider_name,
        display_name=openrouter_model.display_name or provider_model_id,
        context_window=context_window,
        max_output_tokens=max_output_tokens,
        supports_tools=supports_tools,
        supports_images=supports_images,
        thinking=thinking,
        model_type=openrouter_model.model_type or "text",
        extra_params=extra,
    )


def _is_local_provider(ctx: ProviderFetchContext) -> bool:
    provider_name = _normalize_model_name(ctx.provider_name)
    impl = _normalize_model_name(ctx.impl or "")
    if "ollama" in provider_name or "ollama" in impl:
        return True

    if not ctx.base_url:
        return False

    try:
        parsed = urlparse(ctx.base_url)
    except Exception:
        return False
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        ip = ipaddress.ip_address(host)
        return bool(ip.is_private or ip.is_loopback)
    except ValueError:
        pass
    return host.endswith(".local")


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
    """按「OpenRouter 元信息 + Provider 可用模型」同步模型信息。"""
    provider_name = provider["name"].strip().lower()
    ctx = _build_fetch_context(provider)
    logger.info(
        "开始同步模型 provider=%s impl=%s base_url=%s",
        provider_name,
        ctx.impl or provider_name,
        ctx.base_url or "默认",
    )
    if _is_local_provider(ctx):
        logger.info("命中本地 Provider 占位分支 provider=%s", provider_name)
        raise LocalProviderPlaceholderError(provider_name)

    fetcher = _FETCHERS.get(provider_name)
    if fetcher is None:
        logger.error("供应商未注册模型列表抓取器 provider=%s", provider_name)
        raise ValueError(f"不支持的供应商: {provider_name}")

    try:
        model_ids = await fetcher.fetch(ctx)
        logger.info("Provider 模型列表获取成功 provider=%s count=%d", provider_name, len(model_ids))
    except Exception:
        logger.exception("Provider 模型列表获取失败 provider=%s", provider_name)
        raise

    try:
        openrouter_models = await _fetch_openrouter_models()
    except Exception:
        logger.exception("OpenRouter 模型列表获取失败 provider=%s", provider_name)
        raise
    openrouter_index = _build_openrouter_index(openrouter_models)

    merged: list[ModelInfo] = []
    seen: set[str] = set()
    for model_id in model_ids:
        key = _normalize_model_name(model_id)
        if not key or key in seen:
            continue
        openrouter_model = openrouter_index.get(key)
        if openrouter_model is None:
            continue
        merged.append(_clone_from_openrouter(openrouter_model, provider_name, model_id))
        seen.add(key)

    if not merged:
        logger.error("模型交集为空 provider=%s provider_count=%d openrouter_count=%d", provider_name, len(model_ids), len(openrouter_models))
        raise ModelIntersectionEmptyError(provider_name)

    logger.info("模型交集过滤完成 provider=%s count=%d", provider_name, len(merged))
    return merged


def _register_builtin_fetchers() -> None:
    register_model_fetcher("openai", _OpenAICompatibleFetcher("https://api.openai.com"))
    register_model_fetcher("deepseek", _OpenAICompatibleFetcher("https://api.deepseek.com"))
    register_model_fetcher(
        "qwen",
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
