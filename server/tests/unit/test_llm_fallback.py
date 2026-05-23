"""测试 LLM fallback chain.

重构后 chain 元素是 (LLM, LLMCallSpec) 二元组:
    - LLM 是池化的 client (只持有 api_key + SDK)
    - LLMCallSpec 是本次调用的参数 (model/temperature/...)
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from config.domains.llm import LLMCallSpec
from config.domains.quota import UsageQuotaWindowSettings, UserQuotaSettings

from forge.core.request_context import user_id_scope
from forge.llm.cost_tracker import get_cost_tracker
from forge.llm.fallback import LLMFallbackChain
from forge.llm.providers.base import LLM, ChatChunk, ChatMessage, ChatResult
from forge.quota import UserQuotaExceeded, get_usage_quota_manager


# ---------------------------------------------------------------------------
# 辅助: mock LLM client
# ---------------------------------------------------------------------------
class _MockLLM(LLM):
    """可控的 mock LLM, 不实现 tool calling."""

    def __init__(self, name: str, *, fail_first: int = 0, raise_msg: str = "timeout"):
        super().__init__(api_key=f"sk-{name}")
        self._name = name
        self._fail_first = fail_first
        self._calls = 0
        self._raise_msg = raise_msg

    @property
    def provider_name(self) -> str:
        return self._name

    def chat(
        self,
        messages,
        *,
        model: str,
        temperature=None,
        max_tokens=None,
        extra_options=None,
        **kwargs,
    ):
        self._calls += 1
        if self._calls <= self._fail_first:
            raise Exception(self._raise_msg)
        return ChatResult(
            content=f"hello from {self._name}",
            model=model,
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )

    def chat_stream(
        self,
        messages,
        *,
        model: str,
        temperature=None,
        max_tokens=None,
        extra_options=None,
        **kwargs,
    ) -> Iterator[ChatChunk]:
        self._calls += 1
        if self._calls <= self._fail_first:
            raise Exception(self._raise_msg)
        for ch in f"hi-{self._name}":
            yield ChatChunk(delta=ch, finish_reason=None)
        yield ChatChunk(
            delta="", finish_reason="stop", usage={"prompt_tokens": 1, "completion_tokens": 5}
        )


@pytest.fixture(autouse=True)
def _reset_usage_quota():
    get_usage_quota_manager().reset()
    yield
    get_usage_quota_manager().reset()


def _spec(
    name: str,
    *,
    api_key: str = "sk-x",
    model: str | None = None,
    quota_controlled: bool = False,
) -> LLMCallSpec:
    return LLMCallSpec(
        impl=name,
        api_key=api_key,
        model=model or f"{name}-model",
        quota_controlled=quota_controlled,
    )


def _entry(llm: _MockLLM) -> tuple[LLM, LLMCallSpec]:
    return llm, _spec(llm.provider_name, api_key=f"sk-{llm.provider_name}")


# ---------------------------------------------------------------------------
# chat 路径
# ---------------------------------------------------------------------------
def test_chain_uses_primary_when_healthy():
    primary = _MockLLM("p")
    backup = _MockLLM("b")
    chain = LLMFallbackChain(_entry(primary), [_entry(backup)], max_retries=0)
    result = chain.chat([ChatMessage(role="user", content="hi")])
    assert "from p" in result.content
    assert backup._calls == 0


def test_chain_falls_back_when_primary_fails():
    get_cost_tracker().reset()
    primary = _MockLLM("p", fail_first=10)
    backup = _MockLLM("b")
    chain = LLMFallbackChain(
        _entry(primary), [_entry(backup)], max_retries=1, retry_backoff_seconds=0.001
    )
    result = chain.chat([ChatMessage(role="user", content="hi")])
    assert "from b" in result.content
    assert backup._calls == 1


def test_chain_raises_when_all_fail():
    primary = _MockLLM("p", fail_first=10)
    backup = _MockLLM("b", fail_first=10)
    chain = LLMFallbackChain(
        _entry(primary), [_entry(backup)], max_retries=1, retry_backoff_seconds=0.001
    )
    with pytest.raises(Exception, match="timeout"):
        chain.chat([ChatMessage(role="user", content="hi")])


def test_chain_non_retryable_error_skips_retry_but_falls_back():
    primary = _MockLLM("p", fail_first=1, raise_msg="invalid api key")
    backup = _MockLLM("b")
    chain = LLMFallbackChain(
        _entry(primary), [_entry(backup)], max_retries=3, retry_backoff_seconds=0.001
    )
    result = chain.chat([ChatMessage(role="user", content="hi")])
    assert "from b" in result.content
    assert primary._calls == 1


def test_chain_cost_tracker_records_errors():
    get_cost_tracker().reset()
    primary = _MockLLM("err_provider", fail_first=10)
    backup = _MockLLM("ok_provider")
    chain = LLMFallbackChain(
        _entry(primary), [_entry(backup)], max_retries=1, retry_backoff_seconds=0.001
    )
    chain.chat([ChatMessage(role="user", content="hi")])
    snap = get_cost_tracker().snapshot()
    assert "anon:err_provider:err_provider-model" in snap
    assert snap["anon:err_provider:err_provider-model"]["errors"] >= 1
    assert "anon:ok_provider:ok_provider-model" in snap
    assert snap["anon:ok_provider:ok_provider-model"]["errors"] == 0


def test_chain_does_not_apply_user_quota_to_user_configured_provider():
    get_cost_tracker().reset()
    get_usage_quota_manager().configure(
        UserQuotaSettings(
            enabled=True,
            five_hour=UsageQuotaWindowSettings(
                window_seconds=5 * 60 * 60,
                limit_usd=0.000001,
            ),
        )
    )
    primary = _MockLLM("p")
    spec = _spec("p", model="gpt-4o", quota_controlled=False)
    chain = LLMFallbackChain((primary, spec), [], max_retries=0)

    with user_id_scope("u1"):
        chain.chat([ChatMessage(role="user", content="hi")])
        chain.chat([ChatMessage(role="user", content="hi")])

    assert primary._calls == 2


def test_chain_applies_user_quota_to_server_preset_provider():
    get_cost_tracker().reset()
    get_usage_quota_manager().configure(
        UserQuotaSettings(
            enabled=True,
            five_hour=UsageQuotaWindowSettings(
                window_seconds=5 * 60 * 60,
                limit_usd=0.000001,
            ),
        )
    )
    primary = _MockLLM("p")
    spec = _spec("p", model="gpt-4o", quota_controlled=True)
    chain = LLMFallbackChain((primary, spec), [], max_retries=0)

    with user_id_scope("u1"):
        chain.chat([ChatMessage(role="user", content="hi")])
        with pytest.raises(UserQuotaExceeded):
            chain.chat([ChatMessage(role="user", content="hi")])


# ---------------------------------------------------------------------------
# chat_stream 路径
# ---------------------------------------------------------------------------
def test_chain_stream_yields_from_primary_when_healthy():
    primary = _MockLLM("p")
    backup = _MockLLM("b")
    chain = LLMFallbackChain(_entry(primary), [_entry(backup)], max_retries=0)
    chunks = list(chain.chat_stream([ChatMessage(role="user", content="hi")]))
    text = "".join(c.delta for c in chunks)
    assert "p" in text
    assert backup._calls == 0


def test_chain_stream_falls_back_before_first_chunk():
    primary = _MockLLM("p", fail_first=10)
    backup = _MockLLM("b")
    chain = LLMFallbackChain(_entry(primary), [_entry(backup)], max_retries=0)
    chunks = list(chain.chat_stream([ChatMessage(role="user", content="hi")]))
    text = "".join(c.delta for c in chunks)
    assert "b" in text


# ---------------------------------------------------------------------------
# chat_with_tools 路径
# ---------------------------------------------------------------------------
class _ToolMockLLM(_MockLLM):
    """支持 tool_calling 的 mock."""

    @property
    def supports_tool_calling(self) -> bool:
        return True

    def chat_with_tools(
        self,
        messages,
        tools,
        *,
        model: str,
        temperature=None,
        max_tokens=None,
        tool_choice="auto",
        extra_options=None,
        **kwargs,
    ):
        self._calls += 1
        if self._calls <= self._fail_first:
            raise Exception(self._raise_msg)
        return {
            "content": f"hello from {self._name}",
            "tool_calls": [],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "model": model,
        }


def test_chain_chat_with_tools_records_cost():
    get_cost_tracker().reset()
    primary = _ToolMockLLM("p")
    chain = LLMFallbackChain(_entry(primary), [], max_retries=0)
    resp = chain.chat_with_tools([], tools=[])
    assert "from p" in resp["content"]
    snap = get_cost_tracker().snapshot()
    assert "anon:p:p-model" in snap
    assert snap["anon:p:p-model"]["prompt_tokens"] == 10
    assert snap["anon:p:p-model"]["completion_tokens"] == 5
    assert snap["anon:p:p-model"]["errors"] == 0


def test_chain_chat_with_tools_falls_back():
    get_cost_tracker().reset()
    primary = _ToolMockLLM("p", fail_first=10)
    backup = _ToolMockLLM("b")
    chain = LLMFallbackChain(
        _entry(primary), [_entry(backup)], max_retries=1, retry_backoff_seconds=0.001
    )
    resp = chain.chat_with_tools([], tools=[])
    assert "from b" in resp["content"]
    snap = get_cost_tracker().snapshot()
    assert snap["anon:p:p-model"]["errors"] >= 1
    assert snap["anon:b:b-model"]["errors"] == 0


# ---------------------------------------------------------------------------
# CircuitBreaker 集成
# ---------------------------------------------------------------------------
def test_chain_skips_open_breaker_without_calling_provider():
    """primary 的 breaker 已经 OPEN 时, chain 应该直接跳过, 不再调用 provider."""
    from forge.llm.circuit_breaker import (
        BreakerConfig,
        CircuitBreakerRegistry,
        get_breaker_registry,
    )

    get_breaker_registry().reset_all()
    primary = _MockLLM("brkp")
    backup = _MockLLM("brkb")
    primary_spec = _spec("brkp", api_key="sk-brkp")
    backup_spec = _spec("brkb", api_key="sk-brkb")

    # 把 primary 的 breaker 强行打到 OPEN: 用 threshold=1 触发
    reg: CircuitBreakerRegistry = get_breaker_registry()
    breaker = reg.get(primary_spec.impl, primary_spec.api_key, primary_spec.model)
    # 覆盖一个低阈值的临时 breaker 不方便, 直接连发 5 次失败触发默认阈值
    for _ in range(BreakerConfig().failure_threshold):
        breaker.record_failure()

    chain = LLMFallbackChain((primary, primary_spec), [(backup, backup_spec)], max_retries=0)
    result = chain.chat([ChatMessage(role="user", content="hi")])
    assert "from brkb" in result.content
    # primary 一次都没被调用 (breaker 跳过)
    assert primary._calls == 0
    # backup 正常被调用且成功 (并触发 record_success)
    assert backup._calls == 1
    get_breaker_registry().reset_all()


def test_chain_records_failure_to_breaker_on_provider_error():
    """provider 调用失败时, breaker 应记录失败; 累积到阈值后会 OPEN."""
    from forge.llm.circuit_breaker import (
        BreakerConfig,
        BreakerState,
        get_breaker_registry,
    )

    get_breaker_registry().reset_all()
    primary = _MockLLM("brkfail", fail_first=100)
    backup = _MockLLM("brkok")
    primary_spec = _spec("brkfail", api_key="sk-brkfail")
    backup_spec = _spec("brkok", api_key="sk-brkok")

    chain = LLMFallbackChain(
        (primary, primary_spec),
        [(backup, backup_spec)],
        max_retries=0,
        retry_backoff_seconds=0.001,
    )
    threshold = BreakerConfig().failure_threshold
    for _ in range(threshold):
        chain.chat([ChatMessage(role="user", content="hi")])

    breaker = get_breaker_registry().get(
        primary_spec.impl, primary_spec.api_key, primary_spec.model
    )
    assert breaker.state == BreakerState.OPEN
    get_breaker_registry().reset_all()


def test_chain_chat_with_tools_skips_provider_without_support():
    """主 LLM 不支持 tool calling 时, 应自动跳到下一个."""
    no_tool = _MockLLM("no_tool")
    with_tool = _ToolMockLLM("ok")
    chain = LLMFallbackChain(_entry(no_tool), [_entry(with_tool)], max_retries=0)
    resp = chain.chat_with_tools([], tools=[])
    assert "from ok" in resp["content"]
    assert no_tool._calls == 0
