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


def build_chain_from_settings(
    settings,
    *,
    provider: str | None = None,
    model: str | None = None,
    routing_request: RoutingRequest | None = None,
) -> LLMFallbackChain:
    """根据 settings 构建带 fallback 的 LLM 调用链.

    路由优先级 (高 → 低):
        1. 显式 provider / model 入参 (用户 pin / agent.model_id) → 完全绕过 router
        2. routing_request 非空 → 走 CompositeRouter 决策 primary
        3. 都没传 → 用 settings.llm.provider + default_model

    无论 router 是否参与, fallback 链都按 settings.llm.fallback_chain 静态装配.

    实现要点:
        - 主/备 spec 通过 settings.llm.resolve() 解析 (含 api_key 选择)
        - client 走 LLMClientPool, 进程内同 (impl, api_key) 复用同一个 SDK 实例
        - 每次请求都新建一个轻量 LLMFallbackChain 包装 [(client, spec), ...]
    """
    from .client_pool import get_llm_pool
    from .fallback import LLMFallbackChain

    pool = get_llm_pool()

    if provider is None and model is None and routing_request is not None:
        decision = _route_primary(settings, routing_request)
        if decision is not None:
            provider, model = decision.provider, decision.model
            logger.info(
                "Router 决策 primary=%s:%s reason=%s",
                decision.provider,
                decision.model,
                decision.reason,
            )

    primary_spec = settings.llm.resolve(provider, model)
    primary_client = pool.get(
        primary_spec.impl,
        primary_spec.api_key,
        primary_spec.client_options,
    )

    fallbacks: list = []
    for prov, mdl in settings.llm.fallback_pairs():
        try:
            spec = settings.llm.resolve(prov, mdl)
            client = pool.get(spec.impl, spec.api_key, spec.client_options)
            fallbacks.append((client, spec))
        except Exception:
            logger.warning("fallback 装配失败, 跳过: %s:%s", prov, mdl)

    logger.info(
        "LLM 选型 impl=%s model=%s key=%s thinking=%s reasoning_effort=%s "
        "temperature=%s max_tokens=%s quota_controlled=%s fallbacks=%s",
        primary_spec.impl,
        primary_spec.model,
        primary_client.api_key_fingerprint,
        primary_spec.thinking,
        primary_spec.reasoning_effort or "-",
        primary_spec.temperature,
        primary_spec.max_tokens,
        primary_spec.quota_controlled,
        [f"{c.provider_name}:{s.model}" for c, s in fallbacks] or "[]",
    )

    return LLMFallbackChain(
        (primary_client, primary_spec),
        fallbacks,
        max_retries=settings.llm.max_retries,
        retry_backoff_seconds=settings.llm.retry_backoff_seconds,
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
