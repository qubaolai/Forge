"""Phase 4 新功能单元测试: 限流 / 精确缓存 / 幂等 / 舱壁 / 首 Token 超时."""

from __future__ import annotations

import asyncio

import pytest

from forge.llm import LLMRequest, LLMResponse
from forge.llm.caching.exact_cache import InProcessLRUCache, make_cache_key
from forge.llm.inbound_rate_limiter import (
    InboundRateLimitExceeded,
    InboundRateLimiter,
)
from forge.llm.pipeline.cache import (
    CacheWriteMiddleware,
    ExactCacheMiddleware,
)
from forge.llm.pipeline.dedup import (
    DedupCompleteMiddleware,
    DeduplicationMiddleware,
    IdempotencyStore,
)
from forge.llm.pipeline.rate_limit import InboundRateLimitMiddleware
from forge.llm.pipeline.validator import (
    InputValidationError,
    InputValidatorMiddleware,
)
from forge.llm.providers.base import ChatMessage
from forge.llm.resilience.bulkhead import BulkheadRejectError, ProviderBulkhead
from forge.llm.streaming import (
    FirstTokenTimeoutError,
    TimeoutConfig,
    stream_with_first_token_timeout,
)


def _msg(content: str = "hi") -> ChatMessage:
    return ChatMessage(role="user", content=content)


# ----------------------------------------------------------------------
# InputValidator
# ----------------------------------------------------------------------
async def test_validator_rejects_empty_messages():
    mw = InputValidatorMiddleware()
    with pytest.raises(InputValidationError):
        await mw.process(LLMRequest(messages=[]))


async def test_validator_rejects_too_many_messages():
    mw = InputValidatorMiddleware(max_messages=3)
    req = LLMRequest(messages=[_msg() for _ in range(5)])
    with pytest.raises(InputValidationError):
        await mw.process(req)


async def test_validator_rejects_illegal_role():
    mw = InputValidatorMiddleware()
    req = LLMRequest(messages=[ChatMessage(role="hacker", content="x")])
    with pytest.raises(InputValidationError):
        await mw.process(req)


async def test_validator_passes_normal_request():
    mw = InputValidatorMiddleware()
    req = LLMRequest(messages=[_msg("ok")])
    assert await mw.process(req) is None


# ----------------------------------------------------------------------
# Inbound rate limit
# ----------------------------------------------------------------------
async def test_rate_limiter_rpm():
    limiter = InboundRateLimiter(enabled=True, rpm=2)
    assert limiter.check("u1").allow
    assert limiter.check("u1").allow
    res = limiter.check("u1")
    assert not res.allow
    assert "RPM" in res.reason


async def test_rate_limiter_tpm():
    limiter = InboundRateLimiter(enabled=True, tpm=100)
    assert limiter.check("u1", estimated_tokens=60).allow
    res = limiter.check("u1", estimated_tokens=60)
    assert not res.allow
    assert "TPM" in res.reason


async def test_rate_limiter_disabled_passes():
    limiter = InboundRateLimiter(enabled=False, rpm=1)
    for _ in range(10):
        assert limiter.check("u1").allow


async def test_rate_limit_middleware_raises():
    limiter = InboundRateLimiter(enabled=True, rpm=1)
    mw = InboundRateLimitMiddleware(limiter=limiter)
    await mw.process(LLMRequest(messages=[_msg()], user_id="u1"))
    with pytest.raises(InboundRateLimitExceeded):
        await mw.process(LLMRequest(messages=[_msg()], user_id="u1"))


# ----------------------------------------------------------------------
# Exact cache
# ----------------------------------------------------------------------
def test_cache_key_stable_for_same_messages():
    msgs = [_msg("hello"), ChatMessage(role="assistant", content="hi")]
    k1 = make_cache_key("openai", "gpt-4o", msgs)
    k2 = make_cache_key("openai", "gpt-4o", msgs)
    assert k1 == k2
    assert k1.startswith("forge:llm:exact:")


def test_cache_key_differs_for_different_models():
    msgs = [_msg("hello")]
    k1 = make_cache_key("openai", "gpt-4o", msgs)
    k2 = make_cache_key("openai", "gpt-4o-mini", msgs)
    assert k1 != k2


async def test_cache_get_set_lru():
    cache = InProcessLRUCache(max_size=2)
    r1 = LLMResponse(content="a", model="m", provider="p")
    r2 = LLMResponse(content="b", model="m", provider="p")
    r3 = LLMResponse(content="c", model="m", provider="p")
    await cache.set("k1", r1)
    await cache.set("k2", r2)
    await cache.set("k3", r3)  # 淘汰 k1
    assert await cache.get("k1") is None
    assert (await cache.get("k2")).content == "b"
    assert (await cache.get("k3")).content == "c"


async def test_exact_cache_middleware_hit():
    cache = InProcessLRUCache()
    pre = ExactCacheMiddleware(backend=cache)
    post = CacheWriteMiddleware(backend=cache)
    req = LLMRequest(
        messages=[_msg()],
        preferred_provider="openai",
        preferred_model="gpt-4o",
        temperature=0.0,
    )
    # 首次: 无命中
    assert await pre.process(req) is None
    # 写入
    resp = LLMResponse(content="ans", model="gpt-4o", provider="openai")
    await post.process(req, resp)
    # 二次: 命中
    cached = await pre.process(req)
    assert cached is not None
    assert cached.content == "ans"
    assert cached.cache_hit is True
    assert cached.cache_type == "exact"


async def test_cache_skips_non_zero_temperature():
    cache = InProcessLRUCache()
    pre = ExactCacheMiddleware(backend=cache)
    post = CacheWriteMiddleware(backend=cache)
    req = LLMRequest(
        messages=[_msg()],
        preferred_provider="openai",
        preferred_model="gpt-4o",
        temperature=0.7,
    )
    await post.process(req, LLMResponse(content="x", model="gpt-4o", provider="openai"))
    assert cache.size() == 0
    assert await pre.process(req) is None


# ----------------------------------------------------------------------
# Dedup
# ----------------------------------------------------------------------
async def test_dedup_first_call_new():
    store = IdempotencyStore()
    mw = DeduplicationMiddleware(store=store)
    req = LLMRequest(messages=[_msg()], idempotency_key="k1")
    # 首次: 不短路 (返回 None, 让流程继续)
    assert await mw.process(req) is None


async def test_dedup_second_call_after_complete_returns_cached():
    store = IdempotencyStore()
    pre = DeduplicationMiddleware(store=store)
    post = DedupCompleteMiddleware(store=store)
    req = LLMRequest(messages=[_msg()], idempotency_key="k2")
    assert await pre.process(req) is None
    resp = LLMResponse(content="answer", model="m", provider="p")
    await post.process(req, resp)
    # 第二次相同 key: 命中
    cached = await pre.process(req)
    assert cached is not None
    assert cached.content == "answer"


async def test_dedup_no_key_passes_through():
    store = IdempotencyStore()
    mw = DeduplicationMiddleware(store=store)
    assert await mw.process(LLMRequest(messages=[_msg()])) is None


# ----------------------------------------------------------------------
# Bulkhead
# ----------------------------------------------------------------------
async def test_bulkhead_admits_within_limit():
    bk = ProviderBulkhead(max_concurrent_per_provider=2)
    async with bk.guard("openai"):
        async with bk.guard("openai"):
            pass


async def test_bulkhead_rejects_over_limit():
    bk = ProviderBulkhead(max_concurrent_per_provider=1)
    async with bk.guard("openai"):
        with pytest.raises(BulkheadRejectError):
            async with bk.guard("openai"):
                pass


async def test_bulkhead_disabled_passes():
    bk = ProviderBulkhead(max_concurrent_per_provider=0)
    for _ in range(10):
        async with bk.guard("openai"):
            pass


async def test_bulkhead_isolates_providers():
    bk = ProviderBulkhead(max_concurrent_per_provider=1)
    async with bk.guard("openai"):
        # 不同 provider 互不影响
        async with bk.guard("anthropic"):
            pass


# ----------------------------------------------------------------------
# First-token timeout
# ----------------------------------------------------------------------
async def test_first_token_timeout_triggers():
    async def slow_stream():
        await asyncio.sleep(0.5)
        yield  # 永远不到

    with pytest.raises(FirstTokenTimeoutError):
        async for _ in stream_with_first_token_timeout(slow_stream(), 0.05):
            pass


async def test_first_token_timeout_passes_when_fast_enough():
    from forge.llm.providers.base import ChatChunk

    async def fast_stream():
        yield ChatChunk(delta="hello")
        yield ChatChunk(delta="!", finish_reason="stop")

    chunks = [c async for c in stream_with_first_token_timeout(fast_stream(), 5.0)]
    assert len(chunks) == 2
    assert chunks[0].delta == "hello"


def test_timeout_config_defaults():
    cfg = TimeoutConfig()
    assert cfg.connection_timeout_s == 5.0
    assert cfg.first_token_timeout_s == 15.0
    assert cfg.total_timeout_s == 120.0
