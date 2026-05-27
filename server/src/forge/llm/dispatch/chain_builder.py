"""链构造器: 把 settings + provider/model 决策映射成 LLMDispatcher 实例.

两类构链:
    build_dispatch_chain(...)            ── 主模型调用链 (chat / tool_use)
    build_utility_dispatch_chain(...)    ── 工具模型调用链 (摘要/标题/意图等)

回落语义:
    - 主模型: 不做 provider/model fallback. 链内只含同 provider/model 的多 Key 候选;
      只有 429 错误才会切到下一个 key
    - 工具模型: provider/model 级回落 (utility_llm → task_model → default_model)
"""

from __future__ import annotations

import logging
from dataclasses import replace

from forge.config.domains.llm import LLMCallSpec

from ..providers.base import LLM

logger = logging.getLogger(__name__)


async def build_dispatch_chain(
    settings,
    *,
    provider: str | None = None,
    model: str | None = None,
    model_cache=None,
):
    """根据 ModelConfigCache 构建主模型调用链.

    主模型不做 provider/model fallback; 链内只包含同一 provider/model 的 key 级
    候选, 且只有 429 限流错误才会切换到下一个 key.
    """
    from .dispatcher import LLMDispatcher

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
    return LLMDispatcher(
        (primary_client, primary_spec),
        fallbacks,
        max_retries=settings.llm.max_retries,
        retry_backoff_seconds=settings.llm.retry_backoff_seconds,
        timeout_config=settings.llm.timeout,
    )


async def build_utility_dispatch_chain(
    settings,
    *,
    utility_provider: str | None = None,
    utility_model: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    model_cache=None,
):
    """构建工具模型调用链.

    回落顺序:
        utility_llm 配置 → 任务传入 provider/model → 默认模型
    """
    from .dispatcher import LLMDispatcher

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
        raise ValueError("工具模型构建失败, 没有可用 provider/model")

    primary_client, primary_spec = entries[0]
    logger.info(
        "工具模型选型完成: provider=%s impl=%s model=%s fallback_entries=%d",
        primary_spec.provider_name or primary_spec.impl,
        primary_spec.impl,
        primary_spec.model,
        max(0, len(entries) - 1),
    )
    return LLMDispatcher(
        (primary_client, primary_spec),
        entries[1:],
        max_retries=settings.llm.max_retries,
        retry_backoff_seconds=settings.llm.retry_backoff_seconds,
        timeout_config=settings.llm.timeout,
    )


async def _build_entries_from_cache(
    *,
    provider: str | None,
    model: str | None,
    model_cache=None,
) -> list[tuple[LLM, LLMCallSpec]]:
    """从 ModelConfigCache 构建同一 provider/model 的 key 候选链."""
    from ..client_pool import get_llm_pool

    if not provider or not model:
        raise ValueError("构建 LLM 调用链失败: provider/model 必须显式传入")
    if model_cache is None:
        from ..model_config_cache import ModelConfigCache

        model_cache = ModelConfigCache.get_global()
    try:
        cache_ready = await model_cache.is_ready()
    except Exception as exc:  # noqa: BLE001
        logger.exception("ModelConfigCache 就绪检查失败")
        raise ValueError("模型配置缓存不可用, 无法构建 LLM 调用链") from exc
    if not cache_ready:
        logger.error("ModelConfigCache 未就绪, 拒绝构建 LLM 调用链")
        raise ValueError("模型配置缓存未就绪, 请稍后重试")

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
        raise ValueError(f"供应商 {provider} 当前无可用 API Key, 请稍后重试")

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
    """按约定顺序生成工具模型候选, 并去重."""
    raw = [
        (
            utility_provider or settings.utility_llm.provider or "",
            utility_model or settings.utility_llm.model or "",
            "utility_llm",
        ),
        (provider or "", model or "", "task_model"),
        (settings.llm.provider or "", settings.llm.default_model or "", "default_model"),
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


__all__ = [
    "build_dispatch_chain",
    "build_utility_dispatch_chain",
]
