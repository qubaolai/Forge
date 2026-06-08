"""ChainResolver — 模型选择层,取代旧 router/。

优先级:
    1. user_pin(req.preferred_provider + preferred_model 双值)→ web:
       链头 = pin 模型;后备 = 该 provider 的【对话链】去掉 pin 后的同 provider 模型。
       pin 模型自身仍过能力校验,不满足直接 fail-fast(修复旧 user_pin 跳过一切校验)。
    2. 档位(req.model_profile = fast/smart/strong)→ CLI / utility:
       走【档位链】,可跨 provider,保配置顺序。
    3. 都没有 → 系统保底 settings.llm 默认模型。

产出有序 [(provider, model)] 后统一过滤:模型启用 + 能力硬约束(tools/vision/thinking/context)。
key 轮换不在此处——那是供应商级的池职责(client_pool),与模型无关。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _capability_ok(config: dict[str, Any], *, needs_tools: bool, req) -> bool:
    """模型能力硬约束:满足返回 True。"""
    caps = set(config.get("capabilities") or [])
    if needs_tools and "tools" not in caps:
        return False
    if req.requires_vision:
        modalities = set(config.get("input_modalities") or [])
        if "vision" not in caps and "image" not in modalities:
            return False
    if req.requires_thinking and "thinking" not in caps:
        return False
    cw = config.get("context_window", 128000)
    return not (req.estimated_input_tokens and cw < req.estimated_input_tokens)


async def _entry_usable(
    provider: str, model: str, *, needs_tools: bool, req, model_cache
) -> dict | None:
    """返回可用模型详情;不存在/未启用/能力不符返回 None。"""
    if not provider or not model:
        return None
    detail = await model_cache.get_model_detail(provider, model)
    if detail is None or not detail.get("is_enabled"):
        return None
    if detail.get("model_type") not in (None, "chat"):
        return None
    config = detail.get("config") or {}
    if not _capability_ok(config, needs_tools=needs_tools, req=req):
        return None
    return detail


async def resolve_chain(req, settings, model_cache) -> list[tuple[str, str]]:
    """解析有序模型调用链 [(provider, model), ...]。

    Raises:
        ValueError: user_pin 模型不可用/能力不符,或无任何可用候选。
    """
    needs_tools = req.requires_tools or bool(req.tools)

    # ---- 1. user_pin(web)----
    if req.preferred_provider and req.preferred_model:
        provider, model = req.preferred_provider, req.preferred_model
        head = await _entry_usable(
            provider, model, needs_tools=needs_tools, req=req, model_cache=model_cache
        )
        if head is None:
            raise ValueError(
                f"指定模型 {provider}:{model} 不存在/未启用,或不满足本次调用能力要求"
                f"(tools={needs_tools}, vision={req.requires_vision}, thinking={req.requires_thinking})"
            )
        chain: list[tuple[str, str]] = [(provider, model)]
        # 同 provider 对话链作为后备
        conv = await model_cache.get_chain("conversation", provider)
        for e in conv:
            ep, em = e.get("provider"), e.get("model")
            if ep != provider or (ep, em) in chain:
                continue
            if await _entry_usable(ep, em, needs_tools=needs_tools, req=req, model_cache=model_cache):
                chain.append((ep, em))
        logger.info("LLM 选链[user_pin]: head=%s:%s 同provider后备=%d", provider, model, len(chain) - 1)
        return chain

    # ---- 2. 档位链(CLI / utility)----
    if req.model_profile:
        tier_chain = await model_cache.get_chain("tier", req.model_profile)
        chain = []
        for e in tier_chain:
            ep, em = e.get("provider"), e.get("model")
            if (ep, em) in chain:
                continue
            if await _entry_usable(ep, em, needs_tools=needs_tools, req=req, model_cache=model_cache):
                chain.append((ep, em))
        if chain:
            logger.info("LLM 选链[tier:%s]: 模型数=%d 跨provider=%s",
                        req.model_profile, len(chain), len({p for p, _ in chain}) > 1)
            return chain
        logger.info("LLM 档位链[%s]为空或全不可用, 回退系统保底", req.model_profile)

    # ---- 3. 系统保底 ----
    dp = settings.llm.provider or None
    dm = settings.llm.default_model or None
    if dp and dm and await _entry_usable(dp, dm, needs_tools=needs_tools, req=req, model_cache=model_cache):
        logger.info("LLM 选链[default]: %s:%s", dp, dm)
        return [(dp, dm)]
    # 保底也不带能力校验地兜一手(避免能力过滤把唯一兜底也滤掉时彻底无链)
    if dp and dm:
        logger.warning("LLM 系统保底 %s:%s 不满足能力校验, 仍作为最后兜底返回", dp, dm)
        return [(dp, dm)]
    raise ValueError("无法解析模型调用链: 无 user_pin / 档位链 / 系统保底任何可用候选")
