"""链构造器: 把已解析的有序 (provider, model) 链映射成 LLMDispatcher 实例.

构链语义(单层扁平链):
    - 输入是 ChainResolver 产出的有序 [(provider, model), ...](跨模型 fallback 顺序)。
    - 每个 (provider, model) 经 _build_entries_from_cache 展开为该 provider 的多 Key 候选,
      按模型顺序拼接成一条扁平链。
    - dispatcher 遍历:同模型多 key 仅 429 切换(供应商级 key 轮换),
      非 429 / 模型整体失败才降级到下一个模型。
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
    chain: list[tuple[str, str]],
    model_cache=None,
):
    """根据已解析的有序 (provider, model) 链构建 LLMDispatcher.

    Args:
        chain: ChainResolver 产出的有序模型链 [(provider, model), ...]。

    每个模型展开为其 provider 的多 Key 候选并按模型顺序拼接;单个模型构建失败时跳过,
    全部失败才报错。
    """
    from .dispatcher import LLMDispatcher

    entries: list[tuple[LLM, LLMCallSpec]] = []
    errors: list[str] = []
    for provider, model in chain:
        try:
            entries.extend(await _build_entries_from_cache(
                provider=provider, model=model, model_cache=model_cache,
            ))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{provider}:{model} 失败: {exc}")
            logger.warning("模型链候选不可用: provider=%s model=%s error=%s", provider, model, exc)

    if not entries:
        logger.error("LLM 调用链构建失败: %s", "；".join(errors) or "没有可用候选")
        raise ValueError("LLM 调用链构建失败, 没有可用 provider/model")

    primary_client, primary_spec = entries[0]
    # 去重保序的模型链 (provider:model),用于打印「当前链的情况」
    ordered_models: list[str] = []
    for _, s in entries:
        tag = f"{s.provider_name or s.impl}:{s.model}"
        if tag not in ordered_models:
            ordered_models.append(tag)
    logger.info(
        "LLM 调用链构建完成: 主=[%s] 链=[%s] 模型数=%d 总候选(含key)=%d",
        ordered_models[0],
        " → ".join(ordered_models),
        len(ordered_models),
        len(entries),
    )
    return LLMDispatcher(
        (primary_client, primary_spec),
        entries[1:],
        max_retries=settings.llm.max_retries,
        retry_backoff_seconds=settings.llm.retry_backoff_seconds,
        timeout_config=getattr(settings.llm, "timeout", None),
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
    if model_detail.get("model_type") != "chat":
        logger.error(
            "非 Chat 模型不能用于 LLM 调用: provider=%s model=%s type=%s",
            provider,
            model,
            model_detail.get("model_type"),
        )
        raise ValueError(f"模型 {provider}:{model} 不是 Chat 模型")

    keys = await model_cache.get_keys(provider)
    if not keys:
        logger.error("供应商无可用 API Key: provider=%s", provider)
        raise ValueError(f"供应商 {provider} 当前没有可用 API Key")

    config = model_detail.get("config") or {}
    extra = config.get("provider_options") or {}
    impl = provider_info.get("impl") or provider
    base_spec = LLMCallSpec(
        impl=impl,
        api_key="",
        model=model,
        provider_name=provider,
        temperature=extra.get("temperature", 0.7),
        max_tokens=extra.get("max_tokens", config.get("max_output_tokens", 4096)),
        top_p=extra.get("top_p"),
        thinking=extra.get("thinking"),
        reasoning_effort=extra.get("reasoning_effort"),
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


__all__ = [
    "build_dispatch_chain",
]
