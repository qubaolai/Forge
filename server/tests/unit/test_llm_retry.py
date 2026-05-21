"""测试 LLM retry 模块."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from forge.llm.retry import call_with_retry, is_retryable


def test_is_retryable_matches_hints():
    assert is_retryable(Exception("Request timed out"))
    assert is_retryable(Exception("rate limit exceeded"))
    assert is_retryable(Exception("503 Service Unavailable"))
    assert is_retryable(Exception("connection reset"))


def test_is_retryable_rejects_non_retryable():
    assert not is_retryable(Exception("invalid api key"))
    assert not is_retryable(Exception("model not found"))
    assert not is_retryable(ValueError("bad arg"))


def test_call_with_retry_success_first_try():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return "ok"

    assert call_with_retry(fn, max_retries=3, backoff_seconds=0) == "ok"
    assert calls["n"] == 1


def test_call_with_retry_succeeds_after_retries():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise Exception("timeout")
        return "ok"

    with patch("forge.llm.retry.time.sleep") as m:  # 不真睡
        result = call_with_retry(fn, max_retries=3, backoff_seconds=0.01)
    assert result == "ok"
    assert calls["n"] == 3
    assert m.call_count == 2  # 重试 2 次


def test_call_with_retry_gives_up_after_max():
    def fn():
        raise Exception("timeout")

    with (
        patch("forge.llm.retry.time.sleep"),
        pytest.raises(Exception, match="timeout"),
    ):
        call_with_retry(fn, max_retries=2, backoff_seconds=0.01)


def test_call_with_retry_no_retry_on_non_retryable():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise ValueError("invalid")  # 不可重试

    with pytest.raises(ValueError):
        call_with_retry(fn, max_retries=3, backoff_seconds=0)
    assert calls["n"] == 1


def test_call_with_retry_on_retry_callback():
    seen: list[tuple] = []
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 2:
            raise Exception("503 server error")
        return "done"

    with patch("forge.llm.retry.time.sleep"):
        result = call_with_retry(
            fn,
            max_retries=3,
            backoff_seconds=0.01,
            on_retry=lambda a, e, d: seen.append((a, type(e).__name__)),
        )
    assert result == "done"
    assert seen == [(1, "Exception")]
