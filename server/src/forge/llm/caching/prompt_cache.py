"""Provider 原生 Prompt 缓存接入工具.

为什么:
    - Anthropic 支持显式标记 `cache_control: ephemeral`, 命中后 input token 价格降至 10%.
    - OpenAI / DeepSeek 自动 prompt prefix caching (长度 > 1024 tokens 时自动启用),
      代码侧无需做任何事, 只在指标里读出 cached_tokens 用于运维观察.

本模块提供两个能力:
    1. should_inject_cache_control(system: str) -> bool
       Anthropic 判断 system prompt 够长再注入, 避免短 prompt 反而拖慢.
    2. build_anthropic_system(system: str) -> str | list[dict]
       够长返回 [{"type":"text","text":..., "cache_control":{"type":"ephemeral"}}],
       不够长返回原 str (Anthropic SDK 两种格式都接受).
    3. extract_cached_tokens(usage: dict, provider: str) -> int
       从 provider usage dict 里读出"本次命中的 cached input tokens".
       provider 之间字段名不同, 这里统一抹平.

不在本模块做:
    - 自管 KV 缓存 (那是 ExactCache 的事, 本期不做).
    - 缓存命中触发任何回放 (Prompt cache 是 provider 自己做的, 我们只观察).

调用方式 (provider 侧):
    Anthropic: provider 用 build_anthropic_system 包 system 字段;
               读 usage.cache_read_input_tokens / cache_creation_input_tokens.
    OpenAI/DeepSeek: 啥都不用做, 在 fallback._emit_audit 里调 extract_cached_tokens
                     上报 metric 即可.
"""

from __future__ import annotations

# Anthropic 注入 cache_control 的阈值. 单位: 字符. Anthropic 要求 prompt
# 长度 > 1024 tokens 才会真正缓存, 标记短 prompt 不报错只是无效.
# 3000 char 是中英混合下 ~750-1500 tokens 的保守估计.
_ANTHROPIC_CACHE_MIN_CHARS = 3000


def should_inject_cache_control(system: str) -> bool:
    """system prompt 是否值得做 ephemeral 缓存."""
    return bool(system) and len(system) >= _ANTHROPIC_CACHE_MIN_CHARS


def build_anthropic_system(system: str) -> str | list[dict]:
    """构造 Anthropic API 的 system 字段.

    短 prompt: 返回原 str (Anthropic SDK 接受).
    长 prompt: 返回单个 text content block + cache_control.
    """
    if not should_inject_cache_control(system):
        return system
    return [
        {
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def extract_cached_tokens(usage: dict | None, provider: str) -> int:
    """从 provider usage 里读"本次命中的 prompt 缓存 token 数".

    字段差异:
        Anthropic: usage.cache_read_input_tokens
        OpenAI:    usage.prompt_tokens_details.cached_tokens
        DeepSeek:  usage.prompt_cache_hit_tokens
        其他:      默认 0
    """
    if not usage:
        return 0
    p = provider.lower()
    if "anthropic" in p:
        return int(usage.get("cache_read_input_tokens") or 0)
    if "deepseek" in p:
        return int(usage.get("prompt_cache_hit_tokens") or 0)
    if "openai" in p:
        details = usage.get("prompt_tokens_details") or {}
        if isinstance(details, dict):
            return int(details.get("cached_tokens") or 0)
        # SDK 2024+ 返回的是对象
        return int(getattr(details, "cached_tokens", 0) or 0)
    return 0
