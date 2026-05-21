"""PromptCache 工具函数测试.

不连真 Anthropic / OpenAI; 验证:
    - should_inject_cache_control 阈值正确
    - build_anthropic_system 短/长 prompt 两种返回
    - extract_cached_tokens 跨 provider 字段匹配
"""

from __future__ import annotations

from forge.llm.caching.prompt_cache import (
    build_anthropic_system,
    extract_cached_tokens,
    should_inject_cache_control,
)


def test_should_inject_threshold():
    assert should_inject_cache_control("") is False
    assert should_inject_cache_control("x" * 500) is False
    assert should_inject_cache_control("x" * 3000) is True


def test_build_short_system_returns_string():
    out = build_anthropic_system("hi")
    assert isinstance(out, str)
    assert out == "hi"


def test_build_long_system_returns_block_with_cache_control():
    long_text = "你好" * 2000  # 4000 chars
    out = build_anthropic_system(long_text)
    assert isinstance(out, list)
    assert len(out) == 1
    block = out[0]
    assert block["type"] == "text"
    assert block["text"] == long_text
    assert block["cache_control"] == {"type": "ephemeral"}


def test_extract_cached_anthropic():
    assert (
        extract_cached_tokens({"cache_read_input_tokens": 1234, "input_tokens": 2000}, "anthropic")
        == 1234
    )


def test_extract_cached_deepseek():
    assert (
        extract_cached_tokens({"prompt_cache_hit_tokens": 555, "prompt_tokens": 600}, "deepseek")
        == 555
    )


def test_extract_cached_openai_dict():
    usage = {
        "prompt_tokens": 1000,
        "prompt_tokens_details": {"cached_tokens": 500},
    }
    assert extract_cached_tokens(usage, "openai") == 500


def test_extract_cached_openai_object_like():
    class _Details:
        cached_tokens = 200

    usage = {"prompt_tokens": 1000, "prompt_tokens_details": _Details()}
    assert extract_cached_tokens(usage, "openai") == 200


def test_extract_cached_unknown_provider_returns_zero():
    assert extract_cached_tokens({"prompt_tokens": 100}, "unknown") == 0


def test_extract_cached_empty_usage_safe():
    assert extract_cached_tokens(None, "openai") == 0
    assert extract_cached_tokens({}, "anthropic") == 0
