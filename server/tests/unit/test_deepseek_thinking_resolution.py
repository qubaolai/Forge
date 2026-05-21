"""DeepSeekLLM thinking 模式解析单测.

重构后 thinking 改为 per-call 参数, 没有实例字段了.
验证 _build_kwargs 输出的 extra_body.thinking 字段, 优先级:
    1. extra_options.thinking (前端 per-request)   — 最高
    2. thinking 参数 (来自 spec, 即 yaml ModelConfig) — 次之
    3. 默认 True (都没传时)                          — 兜底

并验证最终始终往 extra_body 显式写 enabled/disabled, 不靠服务端默认.
"""

from __future__ import annotations

from forge.llm.providers.openai import DeepSeekLLM


def _make_llm() -> DeepSeekLLM:
    """造一个最小可用的 DeepSeekLLM. 新签名只要 api_key, 不需要 model."""
    return DeepSeekLLM(api_key="test")


def _thinking_body(llm, *, spec_thinking=None, extra_options=None):
    """跑一次 _build_kwargs, 返回 extra_body.thinking dict (None 表示没设)."""
    kw = llm._build_kwargs(
        model="deepseek-chat",
        temperature=None,
        max_tokens=None,
        thinking=spec_thinking,
        extra_options=extra_options,
    )
    return kw.get("extra_body", {}).get("thinking")


# ---------------------------------------------------------------------------
# 优先级 1: extra_options.thinking 覆盖一切
# ---------------------------------------------------------------------------
def test_extra_options_true_overrides_spec_false() -> None:
    """前端传 True, spec 是 False -> 前端胜出, 发 enabled."""
    llm = _make_llm()
    assert _thinking_body(llm, spec_thinking=False, extra_options={"thinking": True}) == {
        "type": "enabled"
    }


def test_extra_options_false_overrides_spec_true() -> None:
    """前端传 False, spec 是 True -> 前端胜出, 发 disabled."""
    llm = _make_llm()
    assert _thinking_body(llm, spec_thinking=True, extra_options={"thinking": False}) == {
        "type": "disabled"
    }


# ---------------------------------------------------------------------------
# 优先级 2: spec 在 extra_options 没传时生效
# ---------------------------------------------------------------------------
def test_spec_true_used_when_no_extra() -> None:
    llm = _make_llm()
    assert _thinking_body(llm, spec_thinking=True, extra_options=None) == {"type": "enabled"}


def test_spec_false_used_when_no_extra() -> None:
    """yaml ModelConfig.thinking=false 必须真的关掉."""
    llm = _make_llm()
    assert _thinking_body(llm, spec_thinking=False, extra_options=None) == {"type": "disabled"}


def test_spec_false_used_when_extra_lacks_key() -> None:
    """extra_options 是空 dict (没 thinking key) 不算覆盖."""
    llm = _make_llm()
    assert _thinking_body(llm, spec_thinking=False, extra_options={}) == {"type": "disabled"}


def test_extra_options_thinking_none_falls_back_to_spec() -> None:
    """显式传 None 跟没传等价."""
    llm = _make_llm()
    assert _thinking_body(llm, spec_thinking=False, extra_options={"thinking": None}) == {
        "type": "disabled"
    }


# ---------------------------------------------------------------------------
# 优先级 3: 都没传时默认 True
# ---------------------------------------------------------------------------
def test_default_true_when_neither_specified() -> None:
    """spec 没 thinking 且前端也没传 -> 默认 enabled."""
    llm = _make_llm()
    assert _thinking_body(llm, spec_thinking=None, extra_options=None) == {"type": "enabled"}


def test_default_true_when_spec_is_none_extra_lacks() -> None:
    llm = _make_llm()
    assert _thinking_body(llm, spec_thinking=None, extra_options={"temperature": 0.5}) == {
        "type": "enabled"
    }


# ---------------------------------------------------------------------------
# 始终显式发送 (不省略让服务端决定)
# ---------------------------------------------------------------------------
def test_extra_body_always_has_thinking() -> None:
    """无论解析结果是什么, extra_body 都显式带 type."""
    llm = _make_llm()
    for spec in (True, False, None):
        for ext in (None, {}, {"thinking": True}, {"thinking": False}):
            body = _thinking_body(llm, spec_thinking=spec, extra_options=ext)
            assert body is not None, f"missing for spec={spec} ext={ext}"
            assert body["type"] in ("enabled", "disabled")
