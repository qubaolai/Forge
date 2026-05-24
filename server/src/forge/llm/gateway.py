"""LLM 工厂 + 构链入口.

注册机制:
    @register_llm("xxx") class XxxLLM(LLM)        ── 类装饰器
    _autoload() 在 import 时触发各 provider 自注册

构造:
    build_llm_client(impl, api_key, client_options) -> LLM
    供 LLMClientPool 使用. 每个 (impl, api_key) 由池缓存复用.

构链:
    build_chain_from_settings(settings, provider=..., model=...) -> LLMFallbackChain
    每次请求都会从 ModelConfigCache 读取 key 并在池中做 key 级选择。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from dataclasses import replace

from .providers.base import LLM

if TYPE_CHECKING:
    from .fallback import LLMFallbackChain
    from .router import RoutingRequest

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, type[LLM]] = {}


def register_llm(provider: str):
    """类装饰器: 把 LLM 子类登记到工厂."""

    def decorator(cls: type[LLM]) -> type[LLM]:
        if not issubclass(cls, LLM):
            raise TypeError(f"@register_llm 只能装饰 LLM 子类, 收到 {cls.__name__}")
        if provider in _REGISTRY:
            raise ValueError(
                f"LLM provider 重复注册: {provider} "
                f"(已存在: {_REGISTRY[provider].__name__}, 新增: {cls.__name__})"
            )
        _REGISTRY[provider] = cls
        return cls

    return decorator


def list_providers() -> list[str]:
    """返回所有已注册的 provider 名."""
    return sorted(_REGISTRY.keys())


def build_llm_client(impl: str, api_key: str, client_options: dict | None = None) -> LLM:
    """工厂函数: 按 impl 名 + api_key + client 级参数构造 LLM client.

    给 LLMClientPool 注入用. client_options 支持 base_url / timeout.
    """
    if impl not in _REGISTRY:
        raise ValueError(f"未注册的 LLM provider: {impl!r}. 已注册: {list_providers()}")
    cls = _REGISTRY[impl]
    return cls(api_key, **(client_options or {}))


async def build_chain_from_settings(
    settings,
    *,
    provider: str | None = None,
    model: str | None = None,
    routing_request: RoutingRequest | None = None,
    model_cache=None,
) -> LLMFallbackChain:
    """根据 ModelConfigCache 构建主模型调用链。

    主模型不做 provider/model fallback；链内只包含同一 provider/model 的 key 级
    候选，且只有 429 限流错误才会切换到下一个 key。
    """
    from .fallback import LLMFallbackChain

    if routing_request is not None:
        logger.warning("LLM 路由请求已忽略: 当前主模型必须显式传入 provider/model")

    entries = await _build_entries_from_cache(
        provider=provider,
        model=model,
        model_cache=model_cache,
    )
    primary_client, primary_spec = entries[0]
    fallbacks = entries[1:]

    logger.info(
        "LLM 主模型选型完成: provider=%s impl=%s model=%s key=%s fallbacks=%d",
        primary_spec.provider_name or primary_spec.impl,
        primary_spec.impl,
        primary_spec.model,
        primary_spec.api_key[:6] + "***" if primary_spec.api_key else "-",
        len(fallbacks),
    )
    return LLMFallbackChain(
        (primary_client, primary_spec),
        fallbacks,
        max_retries=settings.llm.max_retries,
        retry_backoff_seconds=settings.llm.retry_backoff_seconds,
    )


async def build_utility_chain_from_settings(
    settings,
    *,
    utility_provider: str | None = None,
    utility_model: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    model_cache=None,
) -> LLMFallbackChain:
    """构建工具模型调用链。

    回落顺序:
        任务传入 utility provider/model → 任务传入 provider/model → 主模型
    """
    from .fallback import LLMFallbackChain

    candidates = _utility_candidates(settings, utility_provider, utility_model, provider, model)
    entries = []
    errors: list[str] = []
    for candidate_provider, candidate_model, reason in candidates:
        try:
            candidate_entries = await _build_entries_from_cache(
                provider=candidate_provider,
                model=candidate_model,
                model_cache=model_cache,
            )
            entries.extend(candidate_entries)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{reason}={candidate_provider}:{candidate_model} 失败: {exc}")
            logger.warning(
                "工具模型回落候选不可用: reason=%s provider=%s model=%s error=%s",
                reason,
                candidate_provider,
                candidate_model,
                exc,
            )

    if not entries:
        logger.error("工具模型构建失败: %s", "；".join(errors) or "没有可用候选")
        raise ValueError("工具模型构建失败，没有可用 provider/model")

    primary_client, primary_spec = entries[0]
    logger.info(
        "工具模型选型完成: provider=%s impl=%s model=%s fallback_entries=%d",
        primary_spec.provider_name or primary_spec.impl,
        primary_spec.impl,
        primary_spec.model,
        max(0, len(entries) - 1),
    )
    return LLMFallbackChain(
        (primary_client, primary_spec),
        entries[1:],
        max_retries=settings.llm.max_retries,
        retry_backoff_seconds=settings.llm.retry_backoff_seconds,
    )


async def _build_entries_from_cache(
    *,
    provider: str | None,
    model: str | None,
    model_cache=None,
) -> list[tuple[LLM, "LLMCallSpec"]]:
    """从 ModelConfigCache 构建同一 provider/model 的 key 候选链。"""
    from config.domains.llm import LLMCallSpec
    from .client_pool import get_llm_pool

    if not provider or not model:
        raise ValueError("构建 LLM 调用链失败: provider/model 必须显式传入")
    if model_cache is None:
        from .model_config_cache import ModelConfigCache

        model_cache = ModelConfigCache.get_global()
    try:
        cache_ready = await model_cache.is_ready()
    except Exception as exc:  # noqa: BLE001
        logger.exception("ModelConfigCache 就绪检查失败")
        raise ValueError("模型配置缓存不可用，无法构建 LLM 调用链") from exc
    if not cache_ready:
        logger.error("ModelConfigCache 未就绪，拒绝构建 LLM 调用链")
        raise ValueError("模型配置缓存未就绪，请稍后重试")

    enabled = await model_cache.is_model_enabled(provider, model)
    if not enabled:
        logger.error("模型未启用或不存在: provider=%s model=%s", provider, model)
        raise ValueError(f"模型 {provider}:{model} 未启用或不存在")

    provider_info = await model_cache.get_provider(provider)
    if not provider_info:
        logger.error("供应商不存在或未启用: provider=%s", provider)
        raise ValueError(f"供应商 {provider} 不存在或未启用")

    model_detail = await model_cache.get_model_detail(provider, model)
    if not model_detail:
        logger.error("模型详情缺失: provider=%s model=%s", provider, model)
        raise ValueError(f"模型详情缺失: {provider}:{model}")

    keys = await model_cache.get_keys(provider)
    if not keys:
        logger.error("供应商无可用 API Key: provider=%s", provider)
        raise ValueError(f"供应商 {provider} 当前没有可用 API Key")

    extra = model_detail.get("extra_params") or {}
    impl = provider_info.get("impl") or provider
    base_spec = LLMCallSpec(
        impl=impl,
        api_key="",
        model=model,
        provider_name=provider,
        temperature=extra.get("temperature", 0.7),
        max_tokens=extra.get("max_tokens", model_detail.get("max_output_tokens", 4096)),
        top_p=extra.get("top_p"),
        thinking=extra.get("thinking"),
        reasoning_effort=model_detail.get("thinking_default") or extra.get("reasoning_effort"),
        thinking_budget=extra.get("thinking_budget"),
        top_k=extra.get("top_k"),
        base_url=provider_info.get("base_url"),
        timeout=extra.get("timeout", 30),
        extra={
            k: v
            for k, v in extra.items()
            if k not in {
                "temperature",
                "max_tokens",
                "top_p",
                "thinking",
                "reasoning_effort",
                "thinking_budget",
                "top_k",
                "timeout",
            }
        },
    )

    pool = get_llm_pool()
    pool.reconcile_provider(impl, keys, base_spec.client_options)
    candidates = pool.get_candidates_by_impl(impl, base_spec.client_options)
    if not candidates:
        logger.error("供应商当前无可用 Key: provider=%s impl=%s", provider, impl)
        raise ValueError(f"供应商 {provider} 当前无可用 API Key，请稍后重试")

    entries: list[tuple[LLM, LLMCallSpec]] = []
    for client, api_key in candidates:
        entries.append((client, replace(base_spec, api_key=api_key)))
    return entries


def _utility_candidates(
    settings,
    utility_provider: str | None,
    utility_model: str | None,
    provider: str | None,
    model: str | None,
) -> list[tuple[str, str, str]]:
    """按约定顺序生成工具模型候选，并去重。"""
    raw = [
        (
            utility_provider or settings.utility_llm.provider or "",
            utility_model or settings.utility_llm.model or "",
            "utility_llm",
        ),
        (provider or "", model or "", "task_model"),
        (settings.llm.provider or "", settings.llm.default_model or "", "main_model"),
    ]
    result: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for candidate_provider, candidate_model, reason in raw:
        if not candidate_provider or not candidate_model:
            continue
        key = (candidate_provider, candidate_model)
        if key in seen:
            continue
        seen.add(key)
        result.append((candidate_provider, candidate_model, reason))
    return result


def split_provider_model(value: str | None) -> tuple[str | None, str | None]:
    """解析 provider:model。缺失 provider 时返回 (None, model)。"""
    raw = (value or "").strip()
    if not raw:
        return None, None
    if ":" not in raw:
        return None, raw
    provider, model = raw.split(":", 1)
    return provider.strip() or None, model.strip() or None


def _autoload() -> None:
    """import 内置实现, 触发自注册.

    每个 provider 单独 try/except: 某个 SDK 没装时, 该 provider 不可用,
    但不影响其他 provider 和整体 import.
    """
    log = logging.getLogger(__name__)
    for mod_name in ("openai", "anthropic", "google", "mock"):
        try:
            __import__(f"forge.llm.providers.{mod_name}")
        except ImportError as e:
            log.debug("LLM provider %s 未加载 (依赖缺失): %s", mod_name, e)
        except Exception as e:  # noqa: BLE001
            log.warning("LLM provider %s 加载失败: %s", mod_name, e)


_autoload()
