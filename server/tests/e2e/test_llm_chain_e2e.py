"""真实模型端到端测试 — 模型选择链 + 网关真实调用。

默认跳过。运行前置:
    1. export FORGE_E2E=1
    2. 运行环境已配置可用的 DB / Redis,且 providers/models/keys 已落库(真实 API Key)
    3. 可选 export E2E_PROVIDER / E2E_MODEL 指定 pin 模型;否则走 fast 档位链

运行:
    FORGE_E2E=1 .venv/bin/python -m pytest tests/e2e -q

说明:
    - 强制降级(主模型真实失败 → 自动切下一个真实模型)难以稳定地对真实 provider 注入故障,
      作为手动验证项(停掉主模型 Key 后观察日志),不在自动用例内。
"""

from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not os.environ.get("FORGE_E2E"), reason="需 FORGE_E2E=1 + 真实依赖(DB/Redis/Key)"),
]


async def _bootstrap_or_skip():
    """初始化 DB + Redis + 模型缓存;任一不可用则跳过。"""
    from forge.config.settings import get_settings
    from forge.infrastructure.cache.redis_client import RedisClient
    from forge.infrastructure.database.database import (
        bootstrap_schema,
        init_engine,
        session_scope,
    )
    from forge.llm.model_config_cache import ModelConfigCache

    try:
        init_engine()
        await bootstrap_schema()
        redis = RedisClient.from_settings()
        if not await redis.ping():
            pytest.skip("Redis 不可用,跳过 e2e")
        cache = ModelConfigCache.get_global(redis)
        async with session_scope() as db:
            await cache.reload_all(db)
        if not await cache.is_ready():
            pytest.skip("模型缓存未就绪(无已启用 provider/model),跳过 e2e")
        return get_settings(), cache
    except pytest.skip.Exception:
        raise
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"e2e 依赖不可用: {exc}")


async def test_real_completion_via_gateway():
    """真实档位链/pin → 真实调用 → 拿到非空内容。"""
    from forge.llm.gateway import get_llm_gateway
    from forge.llm.providers.base import ChatMessage
    from forge.llm.request import LLMRequest

    settings, _ = await _bootstrap_or_skip()
    gw = get_llm_gateway(settings)
    req = LLMRequest(
        messages=[ChatMessage(role="user", content="用一个词回答:晴朗白天的天空通常是什么颜色?")],
        temperature=0.0,
        max_tokens=32,
        preferred_provider=os.environ.get("E2E_PROVIDER"),
        preferred_model=os.environ.get("E2E_MODEL"),
        model_profile="fast",
        cache_enabled=False,
    )
    resp = await gw.complete(req)
    assert resp.content and resp.content.strip(), "真实调用应返回非空内容"
    assert resp.provider, "应回填实际命中的 provider"


async def test_fast_tier_resolves_to_enabled_chain():
    """fast 档位链解析(真实缓存)→ 全部命中已启用 chat 模型。"""
    from forge.llm.dispatch.chain_resolver import resolve_chain
    from forge.llm.request import LLMRequest

    settings, cache = await _bootstrap_or_skip()
    req = LLMRequest(messages=[], model_profile="fast")
    chain = await resolve_chain(req, settings, cache)
    assert chain, "fast 档位应解析出至少一个可用模型(档位链或系统保底)"
    for provider, model in chain:
        detail = await cache.get_model_detail(provider, model)
        assert detail is not None and detail.get("is_enabled"), f"{provider}:{model} 应为已启用模型"
