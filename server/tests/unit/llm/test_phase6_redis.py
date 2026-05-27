"""Phase 6 测试: Redis-backed 实现 (用 fake redis client mock)."""

from __future__ import annotations

import asyncio

import pytest

from forge.llm import LLMResponse
from forge.llm.caching.exact_cache import RedisExactCache
from forge.llm.inbound_rate_limiter import RedisInboundRateLimiter
from forge.llm.pipeline.dedup import (
    IdempotencyOutcome,
    RedisIdempotencyStore,
)


class _FakeRedis:
    """简易内存版 Redis client, 模拟 Phase 6 用到的接口."""

    def __init__(self) -> None:
        self._kv: dict[str, str] = {}
        self._kv_ttl: dict[str, float] = {}
        self._floats: dict[str, float] = {}
        self._zset: dict[str, dict[str, float]] = {}

    async def ping(self) -> bool:
        return True

    async def get(self, key: str) -> str | None:
        return self._kv.get(key)

    async def set(self, key: str, value: str, ttl: int = 3600) -> bool:
        self._kv[key] = value
        return True

    async def delete(self, *keys: str) -> bool:
        for k in keys:
            self._kv.pop(k, None)
            self._zset.pop(k, None)
            self._floats.pop(k, None)
        return True

    async def set_nx_ex(self, key: str, value: str, ttl_seconds: int) -> bool:
        if key in self._kv:
            return False
        self._kv[key] = value
        return True

    async def incrbyfloat(self, key: str, amount: float, ttl: int = 0) -> float | None:
        new_val = self._floats.get(key, 0.0) + amount
        self._floats[key] = new_val
        return new_val

    async def pipeline_zadd_count(
        self,
        key: str,
        member: str,
        score: float,
        cutoff_score: float,
        ttl: int,
    ) -> tuple[int, bool]:
        zset = self._zset.setdefault(key, {})
        # 淘汰窗口外
        for m in list(zset.keys()):
            if zset[m] <= cutoff_score:
                zset.pop(m, None)
        zset[member] = score
        return len(zset), True

    async def pipeline_zadd_sum_window(
        self,
        key: str,
        member: str,
        score: float,
        cutoff_score: float,
        ttl: int,
    ) -> tuple[list[str], bool]:
        zset = self._zset.setdefault(key, {})
        for m in list(zset.keys()):
            if zset[m] <= cutoff_score:
                zset.pop(m, None)
        zset[member] = score
        return list(zset.keys()), True

    async def zrem(self, key: str, *members: str) -> int:
        zset = self._zset.get(key)
        if not zset:
            return 0
        removed = 0
        for m in members:
            if zset.pop(m, None) is not None:
                removed += 1
        return removed


# ----------------------------------------------------------------------
# RedisExactCache
# ----------------------------------------------------------------------
async def test_redis_exact_cache_set_get():
    rc = _FakeRedis()
    cache = RedisExactCache(rc)
    resp = LLMResponse(content="hi", model="m", provider="p", usage={"prompt_tokens": 10})
    await cache.set("k1", resp, ttl_seconds=60)
    got = await cache.get("k1")
    assert got is not None
    assert got.content == "hi"
    assert got.usage == {"prompt_tokens": 10}


async def test_redis_exact_cache_miss_returns_none():
    cache = RedisExactCache(_FakeRedis())
    assert await cache.get("nonexistent") is None


async def test_redis_exact_cache_handles_corrupt_json():
    rc = _FakeRedis()
    rc._kv["k1"] = "not json{{{"
    cache = RedisExactCache(rc)
    assert await cache.get("k1") is None


# ----------------------------------------------------------------------
# RedisIdempotencyStore
# ----------------------------------------------------------------------
async def test_redis_idem_first_call_is_new():
    store = RedisIdempotencyStore(redis_client=_FakeRedis())
    outcome = await store.begin("k1")
    assert outcome.is_new
    assert outcome.result is None


async def test_redis_idem_second_call_pending_then_complete():
    rc = _FakeRedis()
    store = RedisIdempotencyStore(redis_client=rc)
    # 第一次 begin 占位
    outcome1 = await store.begin("k1")
    assert outcome1.is_new
    # 第二次: PENDING 状态 → 非新, 但 result 为 None
    outcome2 = await store.begin("k1")
    assert not outcome2.is_new
    assert outcome2.result is None
    # 完成
    resp = LLMResponse(content="done", model="m", provider="p")
    await store.finish_success("k1", resp)
    # 第三次: 已完成
    outcome3 = await store.begin("k1")
    assert not outcome3.is_new
    assert outcome3.result is not None
    assert outcome3.result.content == "done"


async def test_redis_idem_wait_for_returns_result():
    rc = _FakeRedis()
    store = RedisIdempotencyStore(redis_client=rc, poll_interval=0.01)
    await store.begin("k1")
    # 模拟另一个协程在短时间内完成
    async def _finisher():
        await asyncio.sleep(0.05)
        await store.finish_success("k1", LLMResponse(content="done", model="m", provider="p"))
    finisher_task = asyncio.create_task(_finisher())
    result = await store.wait_for("k1", timeout=1.0)
    await finisher_task
    assert result is not None
    assert result.content == "done"


async def test_redis_idem_wait_for_timeout():
    rc = _FakeRedis()
    store = RedisIdempotencyStore(redis_client=rc, poll_interval=0.01)
    await store.begin("k1")
    result = await store.wait_for("k1", timeout=0.1)
    assert result is None  # 一直 PENDING


async def test_redis_idem_finish_failure_clears():
    rc = _FakeRedis()
    store = RedisIdempotencyStore(redis_client=rc)
    await store.begin("k1")
    await store.finish_failure("k1")
    # 重新 begin 应该是新调用
    outcome = await store.begin("k1")
    assert outcome.is_new


# ----------------------------------------------------------------------
# RedisInboundRateLimiter
# ----------------------------------------------------------------------
async def test_redis_rate_limiter_rpm():
    rc = _FakeRedis()
    limiter = RedisInboundRateLimiter(rc, enabled=True, rpm=2)
    assert (await limiter.check("u1")).allow
    assert (await limiter.check("u1")).allow
    res = await limiter.check("u1")
    assert not res.allow
    assert "RPM" in res.reason


async def test_redis_rate_limiter_tpm():
    rc = _FakeRedis()
    limiter = RedisInboundRateLimiter(rc, enabled=True, tpm=100)
    assert (await limiter.check("u1", estimated_tokens=60)).allow
    res = await limiter.check("u1", estimated_tokens=60)
    assert not res.allow
    assert "TPM" in res.reason


async def test_redis_rate_limiter_disabled_passes():
    rc = _FakeRedis()
    limiter = RedisInboundRateLimiter(rc, enabled=False, rpm=1)
    for _ in range(5):
        assert (await limiter.check("u1")).allow


async def test_redis_rate_limiter_per_user_isolated():
    rc = _FakeRedis()
    limiter = RedisInboundRateLimiter(rc, enabled=True, rpm=1)
    assert (await limiter.check("u1")).allow
    # 不同用户互不影响
    assert (await limiter.check("u2")).allow
    # u1 第二次被拒
    assert not (await limiter.check("u1")).allow
