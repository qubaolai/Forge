"""LLM 工厂 + 构链入口.

注册机制:
    @register_llm("xxx") class XxxLLM(LLM)        ── 类装饰器
    _autoload() 在 import 时触发各 provider 自注册

构造:
    build_llm_client(impl, api_key, client_options) -> LLM
    供 LLMClientPool 使用. 每个 (impl, api_key) 由池缓存复用.

构链:
    build_chain_from_settings(settings, provider=..., model=...) -> LLMFallbackChain
    每次请求都会调一遍, 但 client 走池, fallback chain 是轻量包装.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

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
    """根据 settings + DB 构建 LLM 调用链。

    当 settings.llm.providers 有配置时走 YAML 解析，
    为空时直接从缓存或环境变量构造（DB 驱动模式）。
    model_cache 不为 None 时，校验 provider:model 是否已启用。
    """
    from .client_pool import get_llm_pool
    from .fallback import LLMFallbackChain
    from .model_config_cache import ModelConfigCache

    pool = get_llm_pool()
    fallbacks: list = []
    if model_cache is None:
        model_cache = ModelConfigCache.get_global()
    cache_ready = False
    try:
        cache_ready = await model_cache.is_ready()
    except Exception:
        cache_ready = False

    # 校验模型启用状态
    if provider and model and model_cache is not None and cache_ready:
        try:
            enabled = await model_cache.is_model_enabled(provider, model)
            if not enabled:
                raise ValueError(
                    f"模型 {provider}:{model} 未启用或不存在，请联系管理员在模型管理中启用"
                )
        except (TypeError, AttributeError):
            # model_cache 不是 async 或方法不存在时降级
            pass

    if settings.llm.providers:
        # ── YAML 模式 ──
        if provider is None and model is None and routing_request is not None:
            decision = _route_primary(settings, routing_request)
            if decision is not None:
                provider, model = decision.provider, decision.model

        try:
            primary_spec = _resolve_from_db_or_yaml(
                settings,
                provider,
                model,
                model_cache if cache_ready else None,
            )
        except Exception:
            if not provider or not model:
                raise
            primary_spec = _resolve_from_env(provider, model, settings)

        primary_client = pool.get(
            primary_spec.impl, primary_spec.api_key, primary_spec.client_options,
        )
        for prov, mdl in settings.llm.fallback_pairs():
            try:
                spec = settings.llm.resolve(prov, mdl)
                client = pool.get(spec.impl, spec.api_key, spec.client_options)
                fallbacks.append((client, spec))
            except Exception:
                logger.warning("fallback 装配失败, 跳过: %s:%s", prov, mdl)
    else:
        # ── 无 YAML / DB 模式: 从缓存或环境变量构造 ──
        if not provider or not model:
            raise ValueError("未配置 LLM provider 且未传入 provider/model 参数")
        primary_spec = await _resolve_from_cache_or_env(
            provider,
            model,
            settings,
            model_cache if cache_ready else None,
            pool,
        )
        primary_client = pool.get(
            primary_spec.impl, primary_spec.api_key, primary_spec.client_options,
        )

    logger.info(
        "LLM 选型 impl=%s model=%s temperature=%s max_tokens=%s fallbacks=%s",
        primary_spec.impl, primary_spec.model,
        primary_spec.temperature, primary_spec.max_tokens,
        [f"{c.provider_name}:{s.model}" for c, s in fallbacks] or "[]",
    )

    return LLMFallbackChain(
        (primary_client, primary_spec),
        fallbacks,
        max_retries=settings.llm.max_retries,
        retry_backoff_seconds=settings.llm.retry_backoff_seconds,
    )


def _resolve_from_env(provider: str, model: str, settings) -> "LLMCallSpec":
    """无 YAML 模式: 从池中选一个可用 key，构造 LLMCallSpec。"""
    import os

    from config.domains.llm import LLMCallSpec
    from .client_pool import get_llm_pool

    pool = get_llm_pool()
    # 先尝试从池中选 key（支持多 Key + 冷却）
    client = pool.get_by_impl(provider)
    if client is not None:
        return LLMCallSpec(
            impl=provider,
            api_key="",  # client 已持有 key
            model=model,
            temperature=0.7,
            max_tokens=4096,
        )

    # 池中无 key: 从环境变量取
    env_key = f"{provider.upper()}_API_KEY"
    api_key = os.environ.get(env_key, "")
    if not api_key:
        raise ValueError(f"环境变量 {env_key} 未设置，且池中无可用 key: provider={provider}")

    return LLMCallSpec(
        impl=provider,
        api_key=api_key,
        model=model,
        temperature=0.7,
        max_tokens=4096,
    )


def _route_primary(settings, request: RoutingRequest):
    """枚举 settings 中所有 (provider, model) 作为候选, 让 router 选一个."""
    from .router import get_default_router

    available = []
    for prov_name, pcfg in settings.llm.providers.items():
        for mcfg in pcfg.models:
            available.append((prov_name, mcfg))
    if not available:
        return None
    return get_default_router().route(request, available)


async def _resolve_from_db_or_yaml(
    settings, provider: str | None, model: str | None, model_cache=None
) -> "LLMCallSpec":
    """优先从 YAML settings.llm.resolve() 或 DB 缓存解析 LLMCallSpec。

    YAML 中能找到 provider:model 就直接用（向后兼容）；
    找不到时从 model_cache 查 DB 配置构建 LLMCallSpec。
    """
    from config.domains.llm import LLMCallSpec

    # 先尝试 YAML
    try:
        spec = settings.llm.resolve(provider, model)
        if spec.model:
            # 校验模型在数据库中已启用
            if model_cache is not None and provider and model:
                try:
                    if not await model_cache.is_model_enabled(provider, model):
                        raise ValueError(f"模型 {provider}:{model} 已被禁用")
                except (TypeError, AttributeError):
                    pass  # cache 不可用则跳过校验
            return spec
    except Exception:
        pass

    # YAML 找不到，从 DB 缓存解析
    if model_cache is not None and provider and model:
        model_detail = await model_cache.get_model_detail(provider, model)
        if model_detail:
            provider_info = await model_cache.get_provider(provider) or {}
            keys = await model_cache.get_keys(provider)
            api_key = keys[0]["api_key"] if keys else ""
            extra = model_detail.get("extra_params") or {}
            return LLMCallSpec(
                impl=provider_info.get("impl", provider),
                api_key=api_key,
                model=model,
                temperature=extra.get("temperature", 0.7),
                max_tokens=extra.get("max_tokens", model_detail.get("max_output_tokens", 4096)),
                top_p=extra.get("top_p"),
                thinking=extra.get("thinking"),
                reasoning_effort=model_detail.get("thinking_default") or extra.get("reasoning_effort"),
                thinking_budget=extra.get("thinking_budget"),
                top_k=extra.get("top_k"),
                base_url=provider_info.get("base_url"),
                timeout=extra.get("timeout", 30),
            )

    raise ValueError(f"找不到模型配置: provider={provider} model={model}")


async def _resolve_from_cache_or_env(
    provider: str, model: str, settings, model_cache=None, pool=None
) -> "LLMCallSpec":
    """DB 缓存或环境变量模式：从缓存或环境变量构造 LLMCallSpec。"""
    from config.domains.llm import LLMCallSpec

    # 优先从缓存解析
    if model_cache is not None:
        try:
            model_detail = await model_cache.get_model_detail(provider, model)
            if model_detail:
                provider_info = await model_cache.get_provider(provider) or {}
                keys = await model_cache.get_keys(provider)
                api_key = keys[0]["api_key"] if keys else ""
                extra = model_detail.get("extra_params") or {}
                return LLMCallSpec(
                    impl=provider_info.get("impl", provider),
                    api_key=api_key,
                    model=model,
                    temperature=extra.get("temperature", 0.7),
                    max_tokens=extra.get("max_tokens", model_detail.get("max_output_tokens", 4096)),
                )
        except (TypeError, AttributeError):
            pass

    # 兜底：环境变量
    import os

    env_key = f"{provider.upper()}_API_KEY"
    api_key = os.environ.get(env_key, "")
    if not api_key and pool is not None:
        client = pool.get_by_impl(provider)
        if client is not None:
            api_key = ""
        else:
            raise ValueError(f"环境变量 {env_key} 未设置，且池中无可用 key: provider={provider}")

    return LLMCallSpec(
        impl=provider,
        api_key=api_key,
        model=model,
        temperature=0.7,
        max_tokens=4096,
    )


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
